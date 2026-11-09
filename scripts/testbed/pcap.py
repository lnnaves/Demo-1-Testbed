"""Minimal reader for the PCAP files written by tcpdump.

Only the IPv4 header fields needed to recognize the same packet in two
captures are extracted (addresses, protocol, ports, IP identification, size)
plus the capture timestamp. The payload is never interpreted.

Fragments of one IP datagram share the IP identification; they are grouped
back into one datagram, timestamped by its first fragment (send side) and by
its last fragment (receive side, when the datagram becomes complete).
"""

from __future__ import annotations

import ipaddress
import struct
from dataclasses import dataclass, field
from pathlib import Path

# Link-layer types written by tcpdump that may appear on bat0/wlan0.
LINKTYPE_ETHERNET = 1
LINKTYPE_RAW = 101
LINKTYPE_LINUX_SLL = 113
LINKTYPE_LINUX_SLL2 = 276

_ETHERTYPE_IPV4 = 0x0800
_ETHERTYPE_VLAN = {0x8100, 0x88A8}
_PROTOCOL_NAMES = {1: "icmp", 6: "tcp", 17: "udp"}
_PORT_PROTOCOLS = {6, 17}
# Fragments of one datagram arrive close together; the Linux reassembly
# timeout (net.ipv4.ipfrag_time) is 30 s. Older groups are never extended.
_FRAGMENT_GROUP_SECONDS = 30.0


class PcapError(Exception):
    """The file is not a PCAP this reader understands."""


@dataclass
class Datagram:
    """One IPv4 datagram seen in a capture (fragments already grouped)."""

    first_seen: float
    last_seen: float
    src: str
    dst: str
    protocol: int
    ip_id: int
    sport: int | None
    dport: int | None
    size: int  # IP payload bytes (all fragments together)
    complete: bool
    fragments: int = 1

    @property
    def protocol_name(self) -> str:
        return _PROTOCOL_NAMES.get(self.protocol, str(self.protocol))


@dataclass
class CaptureContents:
    datagrams: list[Datagram]
    non_ipv4: int = 0
    malformed: int = 0
    linktype: int = 0
    records: int = 0


@dataclass
class _FragmentGroup:
    first_seen: float
    last_seen: float
    src: str
    dst: str
    protocol: int
    ip_id: int
    pieces: dict[int, int] = field(default_factory=dict)  # offset -> length
    total: int | None = None  # known once the last fragment (MF=0) is seen
    sport: int | None = None
    dport: int | None = None

    def complete(self) -> bool:
        if self.total is None:
            return False
        covered = 0
        for offset in sorted(self.pieces):
            if offset > covered:
                return False
            covered = max(covered, offset + self.pieces[offset])
        return covered >= self.total


def _records(data: bytes):
    if len(data) < 24:
        raise PcapError("file is shorter than a PCAP header")
    magic = data[:4]
    if magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1"):
        endian = "<"
    elif magic in (b"\xa1\xb2\xc3\xd4", b"\xa1\xb2\x3c\x4d"):
        endian = ">"
    elif magic == b"\x0a\x0d\x0d\x0a":
        raise PcapError("pcapng is not supported; tcpdump -w writes classic PCAP")
    else:
        raise PcapError("not a PCAP file (unknown magic number)")
    fraction = 1e9 if magic in (b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d") else 1e6
    linktype = struct.unpack(endian + "I", data[20:24])[0] & 0x0FFFFFFF
    offset = 24
    records = []
    while offset + 16 <= len(data):
        seconds, fractional, captured, _original = struct.unpack(endian + "IIII", data[offset:offset + 16])
        offset += 16
        packet = data[offset:offset + captured]
        offset += captured
        if len(packet) < captured:
            break  # truncated last record (capture interrupted mid-write)
        records.append((seconds + fractional / fraction, packet))
    return linktype, records


def _ipv4_payload(linktype: int, packet: bytes) -> bytes | None:
    """Return the IPv4 header onwards, or None for anything that is not IPv4."""
    if linktype == LINKTYPE_ETHERNET:
        if len(packet) < 14:
            return None
        ethertype = struct.unpack(">H", packet[12:14])[0]
        start = 14
        while ethertype in _ETHERTYPE_VLAN and len(packet) >= start + 4:
            ethertype = struct.unpack(">H", packet[start + 2:start + 4])[0]
            start += 4
        return packet[start:] if ethertype == _ETHERTYPE_IPV4 else None
    if linktype == LINKTYPE_LINUX_SLL:
        if len(packet) < 16:
            return None
        return packet[16:] if struct.unpack(">H", packet[14:16])[0] == _ETHERTYPE_IPV4 else None
    if linktype == LINKTYPE_LINUX_SLL2:
        if len(packet) < 20:
            return None
        return packet[20:] if struct.unpack(">H", packet[0:2])[0] == _ETHERTYPE_IPV4 else None
    if linktype == LINKTYPE_RAW:
        return packet if packet[:1] and packet[0] >> 4 == 4 else None
    raise PcapError(f"unsupported link-layer type {linktype}")


def read_pcap(path: str | Path) -> CaptureContents:
    """Read every IPv4 datagram of a PCAP, in capture order."""
    linktype, records = _records(Path(path).read_bytes())
    contents = CaptureContents(datagrams=[], linktype=linktype, records=len(records))
    open_groups: dict[tuple, _FragmentGroup] = {}
    finished: list[_FragmentGroup] = []

    for timestamp, packet in records:
        ip = _ipv4_payload(linktype, packet)
        if ip is None:
            contents.non_ipv4 += 1
            continue
        if len(ip) < 20 or ip[0] >> 4 != 4:
            contents.malformed += 1
            continue
        header_length = (ip[0] & 0x0F) * 4
        total_length = struct.unpack(">H", ip[2:4])[0]
        if header_length < 20 or total_length < header_length:
            contents.malformed += 1
            continue
        ip_id = struct.unpack(">H", ip[4:6])[0]
        flags_offset = struct.unpack(">H", ip[6:8])[0]
        more_fragments = bool(flags_offset & 0x2000)
        fragment_offset = (flags_offset & 0x1FFF) * 8
        protocol = ip[9]
        src = str(ipaddress.IPv4Address(ip[12:16]))
        dst = str(ipaddress.IPv4Address(ip[16:20]))
        payload_length = total_length - header_length

        key = (src, dst, protocol, ip_id)
        group = open_groups.get(key)
        if group is not None and (group.complete() or timestamp - group.first_seen > _FRAGMENT_GROUP_SECONDS):
            finished.append(open_groups.pop(key))
            group = None
        if group is None:
            group = _FragmentGroup(timestamp, timestamp, src, dst, protocol, ip_id)
            open_groups[key] = group
        group.last_seen = timestamp
        group.pieces[fragment_offset] = payload_length
        if not more_fragments:
            group.total = fragment_offset + payload_length
        if fragment_offset == 0 and protocol in _PORT_PROTOCOLS:
            ports = ip[header_length:header_length + 4]
            if len(ports) == 4:
                group.sport, group.dport = struct.unpack(">HH", ports)
        if group.complete():
            finished.append(open_groups.pop(key))

    finished.extend(open_groups.values())
    for group in finished:
        contents.datagrams.append(
            Datagram(
                first_seen=group.first_seen,
                last_seen=group.last_seen,
                src=group.src,
                dst=group.dst,
                protocol=group.protocol,
                ip_id=group.ip_id,
                sport=group.sport,
                dport=group.dport,
                size=group.total if group.total is not None else sum(group.pieces.values()),
                complete=group.complete(),
                fragments=len(group.pieces),
            )
        )
    contents.datagrams.sort(key=lambda datagram: datagram.first_seen)
    return contents
