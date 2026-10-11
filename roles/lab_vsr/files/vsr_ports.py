#!/usr/bin/env python3
"""Lists the VSR's network ports over NETCONF as JSON, in PCI order.

Usage: vsr_ports.py <host> <user>   (password on stdin)
Prints [{"name": "pci-b1s0", "bus-addr": "0000:01:00.0", "mac": ..., "model": ...}, ...]
for every port except virtio NICs (management), sorted by PCI address. Proxmox
passes the link ports in lab_links order, so this order matches the inventory.
"""
import json
import sys
import xml.etree.ElementTree as ET

from ncclient import manager

NP = "urn:6wind:vrouter/network-ports"
FILTER = f'<state xmlns="urn:6wind:vrouter"><network-port xmlns="{NP}"/></state>'


def main():
    host, user = sys.argv[1], sys.argv[2]
    password = sys.stdin.readline().rstrip("\n")
    with manager.connect(host=host, port=830, username=user, password=password, hostkey_verify=False,
                         allow_agent=False, look_for_keys=False, timeout=30,
                         device_params={"name": "default"}) as m:
        reply = m.get(filter=("subtree", FILTER))
    ports = []
    for p in ET.fromstring(reply.data_xml).iter(f"{{{NP}}}network-port"):
        fields = {c.tag.split("}")[1]: (c.text or "") for c in p}
        if "virtio" in fields.get("model", "").lower():
            continue
        ports.append({"name": fields.get("name"), "bus-addr": fields.get("bus-addr"),
                      "mac": fields.get("mac-address"), "model": fields.get("model")})
    json.dump(sorted(ports, key=lambda p: p["bus-addr"]), sys.stdout)


if __name__ == "__main__":
    main()
