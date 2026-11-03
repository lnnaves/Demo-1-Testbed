#!/usr/bin/env python3
"""Proof of concept: does mobility work with DockerSta + adhoc BATMAN-adv?

Temporary diagnostic (week 1 of the plan in CLAUDE.md), not part of the
platform. It reuses scripts/config.yml but overrides positions in memory:

    gcs    fixed at x=0
    drone2 fixed at x=20  (relay)
    drone1 moves 15 -> 300 -> 15

Each line shows, for drone1: position, ping to gcs, 802.11 signal (dBm) per
neighbour and the best BATMAN route per originator as
"<dest>><nexthop>(TQ)", where 01=gcs, 02=drone1, 03=drone2 (last MAC byte).

Phase 1 logs convergence from net.start() until gcs answers ping. Phase 2
moves drone1 with setPosition(). If the signal gets weaker with distance,
setPosition() reaches wmediumd.

    sudo python3 scripts/poc_mobility.py                     # BATMAN defaults
    sudo python3 scripts/poc_mobility.py --orig-interval 200 # OGM every 200 ms
    sudo python3 scripts/poc_mobility.py --convergence-only   # phase 1 + layer diagnostics
    sudo python3 scripts/poc_mobility.py --convergence-only --batctl "bl 0"  # BLA off
"""
from __future__ import annotations

import argparse
import re
import sys
import time

from testbed.config import load_config
from testbed.network import _ping, build_network, cleanup_stale_environment, cleanup_mininet

# (seconds since movement start, x of drone1)
WAYPOINTS = [(0, 15.0), (5, 15.0), (65, 300.0), (75, 300.0), (135, 15.0), (140, 15.0)]
CONVERGENCE_LIMIT_SECONDS = 90
_ROUTE = re.compile(r"\*\s+\S*:(\w\w)\s+\S+\s+\(\s*(\d+)\)\s+\S*:(\w\w)\s")
_STATION = re.compile(r"Station \S*:(\w\w) .*?signal:\s*(-?\d+)", re.S)


def x_at(t: float) -> float:
    for (t0, x0), (t1, x1) in zip(WAYPOINTS, WAYPOINTS[1:]):
        if t <= t1:
            return x0 + (x1 - x0) * (t - t0) / (t1 - t0)
    return WAYPOINTS[-1][1]


def snapshot(node, wlan: str, gcs_ip: str) -> str:
    reachable = _ping(node, gcs_ip)
    dump = node.cmd(f"iw dev {wlan} station dump")
    stations = re.split(r"(?=Station )", dump or "")
    signals = " ".join(f"{m.group(1)}:{m.group(2)}" for s in stations if (m := _STATION.search(s))) or "-"
    routes = " ".join(f"{m.group(1)}>{m.group(3)}({m.group(2)})"
                      for m in _ROUTE.finditer(node.cmd("batctl o 2>&1") or "")) or "-"
    return f"ping={'OK' if reachable else 'no'} signal_dBm=[{signals}] routes=[{routes}]"


def layer_diagnostics(sender, receiver, gcs_ip: str, sender_ip: str, gcs_bat0_mac: str) -> str:
    """Which layer blocks IP ping while BATMAN already has a route?"""
    l2 = sender.cmd(f"batctl ping -c 1 -t 1 {gcs_bat0_mac} >/dev/null 2>&1 && echo __L2_OK__")
    l2_ok = "__L2_OK__" in [line.strip() for line in (l2 or "").splitlines()]
    neigh_tx = one_line(sender.cmd(f"ip neigh show {gcs_ip} dev bat0"))
    neigh_rx = one_line(receiver.cmd(f"ip neigh show {sender_ip} dev bat0"))
    tt = one_line(sender.cmd("batctl tg 2>&1 | tail -n +3 | wc -l"))
    return f"batctl_ping={'OK' if l2_ok else 'no'} tt_global={tt} arp@drone1=[{neigh_tx}] arp@gcs=[{neigh_rx}]"


def one_line(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip() and "\x1b" not in line]
    return " | ".join(lines) if lines else "-"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--orig-interval", type=int, help="BATMAN OGM interval in ms on every node")
    parser.add_argument("--batctl", action="append", default=[], metavar="ARGS",
                        help='batctl arguments run on every node after start, e.g. "bl 0" (repeatable)')
    parser.add_argument("--convergence-only", action="store_true", help="stop after phase 1, with layer diagnostics")
    args = parser.parse_args()

    config = load_config()
    positions = {"gcs": [0.0, 0.0, 0.0], "drone2": [20.0, 0.0, 0.0], "drone1": [15.0, 0.0, 0.0]}
    for name, position in positions.items():
        config["nodes"][name]["position"] = position

    gcs_ip = config["nodes"]["gcs"]["address"]
    wlan = f"{config['nodes']['drone1']['container_name']}-wlan0"

    cleanup_stale_environment(config)
    network = None
    try:
        start = time.monotonic()
        network = build_network(config)
        print(f"net.start() + IPs took {time.monotonic() - start:.1f}s", flush=True)
        if args.orig_interval:
            for node in network.nodes.values():
                node.cmd(f"batctl it {args.orig_interval}")
            print(f"BATMAN orig_interval set to {args.orig_interval} ms", flush=True)
        for batctl_args in args.batctl:
            for node in network.nodes.values():
                node.cmd(f"batctl {batctl_args}")
            setting = batctl_args.split()[0]
            print(f"batctl {batctl_args} -> now: {one_line(network.nodes['gcs'].cmd(f'batctl {setting}'))}", flush=True)
        drone1 = network.nodes["drone1"]
        gcs = network.nodes["gcs"]
        gcs_bat0_mac = one_line(gcs.cmd("cat /sys/class/net/bat0/address"))
        drone1_ip = config["nodes"]["drone1"]["address"]

        print("\n=== Phase 1: convergence (drone1 at x=15) ===", flush=True)
        phase_start = time.monotonic()
        while True:
            t = time.monotonic() - phase_start
            line = snapshot(drone1, wlan, gcs_ip)
            if args.convergence_only:
                line += " " + layer_diagnostics(drone1, gcs, gcs_ip, drone1_ip, gcs_bat0_mac)
            print(f"t={t:5.1f}s {line}", flush=True)
            if line.startswith("ping=OK"):
                break
            if t > CONVERGENCE_LIMIT_SECONDS:
                print("mesh did not converge; aborting", flush=True)
                return 1
            time.sleep(max(0.0, 1.0 - (time.monotonic() - phase_start - t)))
        if args.convergence_only:
            return 0

        print("\n=== Phase 2: mobility (drone1 15 -> 300 -> 15) ===", flush=True)
        move_start = time.monotonic()
        while True:
            t = time.monotonic() - move_start
            if t > WAYPOINTS[-1][0]:
                break
            x = x_at(t)
            drone1.setPosition(f"{x:.2f},0,0")
            print(f"t={t:5.1f}s x={x:6.1f} {snapshot(drone1, wlan, gcs_ip)}", flush=True)
            time.sleep(max(0.0, 1.0 - (time.monotonic() - move_start - t)))
        return 0
    finally:
        if network is not None:
            network.stop()
        cleanup_mininet()


if __name__ == "__main__":
    sys.exit(main())
