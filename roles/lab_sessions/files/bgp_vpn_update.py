#!/usr/bin/env python3
"""Writes a BGP RAW update file for BNG Blaster announcing VPN default routes.

Usage: bgp_vpn_update.py <file> <next-hop IPv4> <route-distinguisher> <route-target>
                         <label-ipv4> <label-ipv6> [<route-distinguisher> <route-target> ...]
For each VPN (a group of four arguments), announces 0.0.0.0/0 (VPNv4) and ::/0 (VPNv6,
6VPE with an IPv4-mapped next hop) with its MPLS labels, RD and route target, then
End-of-RIB for both families.
BNG Blaster's own bgpupdate tool does not generate VPN routes.
"""
import ipaddress
import struct
import sys

AFI_IPV4, AFI_IPV6, SAFI_VPN = 1, 2, 128


def message(msg_type, body):
    return b"\xff" * 16 + struct.pack("!HB", 19 + len(body), msg_type) + body


def attribute(flags, code, value):
    if len(value) > 255:
        return struct.pack("!BBH", flags | 0x10, code, len(value)) + value
    return struct.pack("!BBB", flags, code, len(value)) + value


def rd_bytes(rd):  # type 0: 2-byte AS : 4-byte number
    asn, number = (int(x) for x in rd.split(":"))
    return struct.pack("!HHI", 0, asn, number)


def update(afi, next_hop, prefix, label, rd, rt):
    asn, number = (int(x) for x in rt.split(":"))
    net = ipaddress.ip_network(prefix)
    nlri = (struct.pack("!B", 24 + 64 + net.prefixlen)
            + struct.pack("!I", (label << 4) | 1)[1:]          # 20-bit label, bottom of stack
            + rd_bytes(rd) + net.network_address.packed[:(net.prefixlen + 7) // 8])
    nh = b"\x00" * 8 + next_hop                                 # VPN next hop: RD of zero + address
    mp_reach = struct.pack("!HBB", afi, SAFI_VPN, len(nh)) + nh + b"\x00" + nlri
    attrs = (attribute(0x40, 1, b"\x00")                        # ORIGIN IGP
             + attribute(0x40, 2, b"")                          # AS_PATH (empty: iBGP)
             + attribute(0x40, 5, struct.pack("!I", 100))       # LOCAL_PREF
             + attribute(0xC0, 16, struct.pack("!BBHI", 0x00, 0x02, asn, number))  # route target
             + attribute(0x80, 14, mp_reach))
    return message(2, struct.pack("!H", 0) + struct.pack("!H", len(attrs)) + attrs)


def end_of_rib(afi):
    attrs = attribute(0x80, 15, struct.pack("!HB", afi, SAFI_VPN))
    return message(2, struct.pack("!H", 0) + struct.pack("!H", len(attrs)) + attrs)


def main():
    path, nh4, vpns = sys.argv[1], ipaddress.IPv4Address(sys.argv[2]), sys.argv[3:]
    if not vpns or len(vpns) % 4:
        sys.exit("give the route distinguisher, route target and IPv4 and IPv6 labels of each VPN")
    nh6 = ipaddress.IPv6Address(f"::ffff:{nh4}")
    data = b""
    for i in range(0, len(vpns), 4):
        rd, rt, label4, label6 = vpns[i:i + 4]
        data += (update(AFI_IPV4, nh4.packed, "0.0.0.0/0", int(label4), rd, rt)
                 + update(AFI_IPV6, nh6.packed, "::/0", int(label6), rd, rt))
    data += end_of_rib(AFI_IPV4) + end_of_rib(AFI_IPV6)
    with open(path, "wb") as f:
        f.write(data)


if __name__ == "__main__":
    main()
