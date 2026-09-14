#!/usr/bin/env python3

import os
import shlex
import subprocess

from containernet.net import Containernet
from containernet.node import DockerSta
from mininet.log import info, setLogLevel
from mn_wifi.cli import CLI
from mn_wifi.link import adhoc, wmediumd
from mn_wifi.telemetry import telemetry
from mn_wifi.wmediumdConnector import interference


# ====== Project paths ======
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))

# Host directory containing the compiled EDHOC binaries.
BIN_HOST_DIR = os.path.join(PROJECT_ROOT, "bin")

# Path where bin/ will be mounted inside every container.
BIN_CONTAINER_DIR = "/opt/edhoc-bin"


# ====== Docker configuration ======
NODE_IMAGE = "drone:latest"


# ====== Network configuration ======
BSSID_CELL = "02:11:22:33:44:55"
SSID = "adhocNet"
CHANNEL = 5

WLAN_MTU = 1500
BAT_MTU_DESIRED = 5000
MTU_FALLBACK = 1500

PROP_MODEL = "logDistance"
PROP_EXP = 3.5

NOISE_TH = -91
FADING_COF = 3


# ====== Node configuration ======
NODE_NAMES = ["dr1", "dr2", "dr3", "dr4", "gcs0"]

DRONES = [
    ("dr1", "00:00:00:00:00:01", "10.0.0.1/8", "10,20,0"),
    ("dr2", "00:00:00:00:00:02", "10.0.0.2/8", "30,40,0"),
    ("dr3", "00:00:00:00:00:03", "10.0.0.3/8", "27,33,0"),
    ("dr4", "00:00:00:00:00:04", "10.0.0.4/8", "20,30,0"),
]

BAT_IPS = {
    "dr1": "192.168.123.1/24",
    "dr2": "192.168.123.2/24",
    "dr3": "192.168.123.3/24",
    "dr4": "192.168.123.4/24",
    "gcs0": "192.168.123.5/24",
}


def validate_environment():
    """Validate the host files required by the topology."""

    if not os.path.isdir(BIN_HOST_DIR):
        raise RuntimeError(
            f"Binary directory not found: {BIN_HOST_DIR}\n"
            "Build the EDHOC binaries before starting the topology."
        )

    expected_binaries = [
        os.path.join(BIN_HOST_DIR, "edhoc", "initiator"),
        os.path.join(BIN_HOST_DIR, "edhoc", "responder"),
        os.path.join(BIN_HOST_DIR, "pq-edhoc", "initiator"),
        os.path.join(BIN_HOST_DIR, "pq-edhoc", "responder"),
    ]

    for binary in expected_binaries:
        if not os.path.isfile(binary):
            info(f"*** WARNING: binary not found: {binary}\n")
        elif not os.access(binary, os.X_OK):
            info(f"*** WARNING: binary is not executable: {binary}\n")


def run(node, command: str, description: str = "", must_succeed: bool = True):
    """Run a command inside a node and check common error messages."""

    output = node.cmd(command)
    lower_output = (output or "").lower()

    error_markers = [
        "not found",
        "no such file",
        "cannot",
        "failed",
        "error",
        "operation not permitted",
        "invalid argument",
        "does not exist",
        "cannot find device",
    ]

    has_error = any(marker in lower_output for marker in error_markers)

    if has_error:
        info(
            f"\n*** ERROR on {node.name}: "
            f"{description or command}\n"
        )
        info(f"*** Command: {command}\n")
        info(f"*** Output:\n{output}\n")

        if must_succeed:
            raise RuntimeError(
                f"Command failed on {node.name}: "
                f"{description or command}"
            )

        info(
            f"*** WARNING: continuing despite the error "
            f"on {node.name}\n"
        )

    return output


def ensure_iface_exists(node, interface: str):
    """Check whether a network interface exists."""

    run(
        node,
        f"ip link show dev {shlex.quote(interface)}",
        f"check whether {interface} exists",
    )


def force_adhoc_cell(node, interface: str):
    """Force an interface to use the configured ad-hoc cell."""

    quoted_interface = shlex.quote(interface)

    run(
        node,
        "command -v iwconfig",
        "check whether iwconfig is installed",
    )

    run(
        node,
        f"ip link set dev {quoted_interface} down",
        f"bring {interface} down",
        must_succeed=False,
    )

    run(
        node,
        (
            f"iwconfig {quoted_interface} "
            f"mode ad-hoc "
            f"essid {shlex.quote(SSID)} "
            f"ap {shlex.quote(BSSID_CELL)} "
            f"channel {CHANNEL}"
        ),
        f"configure ad-hoc mode on {interface}",
    )

    run(
        node,
        f"ip link set dev {quoted_interface} up",
        f"bring {interface} up",
        must_succeed=False,
    )


def set_mtu_required(node, interface: str, mtu: int):
    """Set an interface MTU and fail if it cannot be configured."""

    ensure_iface_exists(node, interface)

    run(
        node,
        f"ip link set dev {shlex.quote(interface)} mtu {mtu}",
        f"set MTU {mtu} on {interface}",
    )


def set_mtu_tolerant(
    node,
    interface: str,
    desired_mtu: int,
    fallback_mtu: int,
):
    """Try the desired MTU and use a fallback if it is unsupported."""

    ensure_iface_exists(node, interface)

    output = run(
        node,
        (
            f"ip link set dev {shlex.quote(interface)} "
            f"mtu {desired_mtu}"
        ),
        f"set MTU {desired_mtu} on {interface}",
        must_succeed=False,
    )

    lower_output = (output or "").lower()

    failure_markers = [
        "mtu greater than device maximum",
        "invalid argument",
        "operation not permitted",
        "failed",
        "error",
    ]

    if any(marker in lower_output for marker in failure_markers):
        info(
            f"*** {node.name}: {interface} did not accept "
            f"MTU {desired_mtu}. Using MTU {fallback_mtu}.\n"
        )

        run(
            node,
            (
                f"ip link set dev {shlex.quote(interface)} "
                f"mtu {fallback_mtu}"
            ),
            f"set fallback MTU {fallback_mtu} on {interface}",
        )


def assign_bat0_ip(node, ip_cidr: str):
    """Configure a node IP address on bat0."""

    ensure_iface_exists(node, "bat0")

    run(
        node,
        "ip link set dev bat0 up",
        "bring bat0 up",
        must_succeed=False,
    )

    run(
        node,
        (
            "ip addr flush dev bat0 && "
            f"ip addr add {shlex.quote(ip_cidr)} dev bat0"
        ),
        f"assign {ip_cidr} to bat0",
    )


def topology():
    setLogLevel("info")
    validate_environment()

    info(
        f"*** Mounting binaries: "
        f"{BIN_HOST_DIR} -> {BIN_CONTAINER_DIR}\n"
    )

    binary_volume = [
        f"{BIN_HOST_DIR}:{BIN_CONTAINER_DIR}:ro",
    ]

    net = Containernet(
        link=wmediumd,
        wmediumd_mode=interference,
        noise_th=NOISE_TH,
        fading_cof=FADING_COF,
    )

    stations = {}

    info("*** Adding Docker stations\n")

    for name, mac, ip_address, position in DRONES:
        stations[name] = net.addStation(
            name,
            cls=DockerSta,
            mac=mac,
            ip=ip_address,
            position=position,
            dimage=NODE_IMAGE,
            privileged=True,
            mem_limit="4g",
            range=25,
            txpower=10,
            volumes=binary_volume,
        )

    stations["gcs0"] = net.addStation(
        "gcs0",
        cls=DockerSta,
        mac="00:00:00:00:00:05",
        ip="10.0.0.5/8",
        position="10,10,0",
        dimage=NODE_IMAGE,
        privileged=True,
        mem_limit="16g",
        volumes=binary_volume,
    )

    net.setPropagationModel(
        model=PROP_MODEL,
        exp=PROP_EXP,
    )

    info("*** Configuring nodes\n")
    net.configureNodes()

    info("*** Creating BATMAN-adv ad-hoc links\n")

    for name in NODE_NAMES:
        net.addLink(
            stations[name],
            cls=adhoc,
            intf=f"{name}-wlan0",
            ssid=SSID,
            proto="batman_adv",
            mode="g",
            channel=CHANNEL,
            ht_cap="HT40+",
        )

    info("*** Starting network\n")
    net.start()

    info("*** Checking mounted binaries\n")

    for name in NODE_NAMES:
        run(
            stations[name],
            f"test -d {shlex.quote(BIN_CONTAINER_DIR)}",
            "check the mounted binary directory",
        )

    info("*** Checking WLAN interfaces\n")

    for name in NODE_NAMES:
        ensure_iface_exists(
            stations[name],
            f"{name}-wlan0",
        )

    info("*** Configuring the ad-hoc cell\n")

    for name in NODE_NAMES:
        force_adhoc_cell(
            stations[name],
            f"{name}-wlan0",
        )

    info("*** Configuring WLAN MTU\n")

    for name in NODE_NAMES:
        set_mtu_required(
            stations[name],
            f"{name}-wlan0",
            WLAN_MTU,
        )

    info("*** Configuring bat0 MTU\n")

    for name in NODE_NAMES:
        set_mtu_tolerant(
            stations[name],
            "bat0",
            BAT_MTU_DESIRED,
            MTU_FALLBACK,
        )

    info("*** Assigning IP addresses to bat0\n")

    for name, ip_address in BAT_IPS.items():
        assign_bat0_ip(
            stations[name],
            ip_address,
        )

    info("*** Starting telemetry\n")

    telemetry(
        nodes=net.stations,
        single=True,
        data_type="position",
    )

    info("*** Running Containernet CLI\n")

    try:
        CLI(net)
    finally:
        info("*** Stopping network\n")
        net.stop()


if __name__ == "__main__":
    try:
        topology()
    finally:
        print("\nCleaning the Mininet environment...")

        subprocess.run(
            ["mn", "-c"],
            check=False,
        )
