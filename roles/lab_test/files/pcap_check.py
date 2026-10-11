#!/usr/bin/env python3
"""Counts the subscriber signalling in a pcap, and optionally splits it by IP version.

Usage: pcap_check.py <pcap> [<ipv4-out.pcap> <ipv6-out.pcap>]
Prints {"packets": n, "messages": {"PADI": 1, "LCP": 6, "DHCP Request": 2, ...}} for the
PPPoE discovery and session protocols (LCP, PAP, IPCP, IPv6CP), DHCPv4 and DHCPv6 messages
and ICMPv6 router solicitations and advertisements, skipping any VLAN tags. With two output
files it also writes the IPv4 (and ARP) and the IPv6 packets to each. Standard library only.
"""
import json
import struct
import sys
from collections import Counter

PPPOE_DISCOVERY = {0x09: "PADI", 0x07: "PADO", 0x19: "PADR", 0x65: "PADS", 0xA7: "PADT"}
PPP = {0xC021: "LCP", 0xC023: "PAP", 0x8021: "IPCP", 0x8057: "IPv6CP"}
DHCP = {1: "DHCP Discover", 2: "DHCP Offer", 3: "DHCP Request", 4: "DHCP Decline", 5: "DHCP Ack",
        6: "DHCP Nak", 7: "DHCP Release"}
DHCPV6 = {1: "DHCPv6 Solicit", 2: "DHCPv6 Advertise", 3: "DHCPv6 Request", 5: "DHCPv6 Renew",
          6: "DHCPv6 Rebind", 7: "DHCPv6 Reply", 8: "DHCPv6 Release", 11: "DHCPv6 Information-request",
          12: "DHCPv6 Relay-forward", 13: "DHCPv6 Relay-reply"}
ICMPV6 = {133: "Router Solicitation", 134: "Router Advertisement"}


def ipv4_message(packet):
    if len(packet) < 28 or packet[9] != 17:
        return None
    header = (packet[0] & 0x0F) * 4
    sport, dport = struct.unpack("!HH", packet[header:header + 4])
    if {sport, dport} & {67, 68}:
        options = packet[header + 8 + 240:]
        i = 0
        while i + 1 < len(options) and options[i] != 255:
            if options[i] == 0:
                i += 1
                continue
            if options[i] == 53:
                return DHCP.get(options[i + 2], "DHCP")
            i += 2 + options[i + 1]
        return "DHCP"
    return None


def ipv6_message(packet):
    if len(packet) < 48:
        return None
    if packet[6] == 58:
        return ICMPV6.get(packet[40])
    if packet[6] == 17:
        sport, dport = struct.unpack("!HH", packet[40:44])
        if {sport, dport} & {546, 547}:
            return dhcpv6_message(packet[48:])
    return None


def dhcpv6_message(message):
    """The DHCPv6 message type; for a relayed message (from a lightweight relay agent such
    as BNG Blaster's LDRA), the type of the client or server message it carries."""
    if message[0] in (12, 13):
        options = message[34:]  # msg-type, hop-count, link-address, peer-address
        while len(options) >= 4:
            code, length = struct.unpack("!HH", options[:4])
            if code == 9 and length:  # Relay Message
                return dhcpv6_message(options[4:4 + length])
            options = options[4 + length:]
    return DHCPV6.get(message[0], "DHCPv6")


def classify(frame):
    """(IP version or None, message name or None) for one Ethernet frame."""
    offset, ethertype = 12, struct.unpack("!H", frame[12:14])[0]
    while ethertype in (0x8100, 0x88A8, 0x9100):
        offset += 4
        ethertype = struct.unpack("!H", frame[offset:offset + 2])[0]
    payload = frame[offset + 2:]
    if ethertype == 0x8863 and len(payload) > 1:
        return None, PPPOE_DISCOVERY.get(payload[1], "PPPoE discovery")
    if ethertype == 0x8864 and len(payload) > 7:
        protocol = struct.unpack("!H", payload[6:8])[0]
        if protocol in PPP:
            return None, PPP[protocol]
        if protocol == 0x0021:
            return 4, ipv4_message(payload[8:])
        if protocol == 0x0057:
            return 6, ipv6_message(payload[8:])
        return None, None
    if ethertype == 0x0806:
        return 4, "ARP"
    if ethertype == 0x0800:
        return 4, ipv4_message(payload)
    if ethertype == 0x86DD:
        return 6, ipv6_message(payload)
    return None, None


def main():
    with open(sys.argv[1], "rb") as f:
        data = f.read()
    header, offset = data[:24], 24
    endian = "<" if data[:4] == b"\xd4\xc3\xb2\xa1" else ">"
    split = sys.argv[2:4] if len(sys.argv) > 3 else None
    outputs = {4: [header], 6: [header]}
    messages, packets = Counter(), 0
    while offset + 16 <= len(data):
        length = struct.unpack(endian + "I", data[offset + 8:offset + 12])[0]
        record = data[offset:offset + 16 + length]
        version, message = classify(record[16:])
        packets += 1
        if message:
            messages[message] += 1
        if version:
            outputs[version].append(record)
        offset += 16 + length
    if split:
        for version, path in zip((4, 6), split):
            with open(path, "wb") as f:
                f.write(b"".join(outputs[version]))
    print(json.dumps({"packets": packets, "messages": dict(messages)}))


if __name__ == "__main__":
    main()
