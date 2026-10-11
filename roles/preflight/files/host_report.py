#!/usr/bin/env python3
"""Read-only host inspection for preflight. Prints one JSON document.

Usage: host_report.py '<lab_links as JSON>'
Reports host capabilities (CPU, IOMMU, Proxmox) and, for every port in the
looped links, its driver, network interfaces, whether the host uses it and its
IOMMU group. Then judges the lot: "problems" block the lab, "warnings" don't.
"""
import json
import os
import re
import subprocess
import sys

SYS_PCI = "/sys/bus/pci/devices"


def read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def link_name(path):
    try:
        return os.path.basename(os.readlink(path))
    except OSError:
        return None


def ip_json(*args):
    try:
        out = subprocess.run(["ip", "-j", *args], capture_output=True, text=True, check=True).stdout
        return json.loads(out or "[]")
    except (OSError, subprocess.CalledProcessError, ValueError):
        return []


def host_info():
    cpuinfo = read("/proc/cpuinfo", "")
    flags = set(re.search(r"^flags\s*:\s*(.*)$", cpuinfo, re.M).group(1).split()) if "flags" in cpuinfo else set()
    vendor = re.search(r"^vendor_id\s*:\s*(\S+)", cpuinfo, re.M)
    groups = os.listdir("/sys/kernel/iommu_groups") if os.path.isdir("/sys/kernel/iommu_groups") else []
    try:
        pve = subprocess.run(["pveversion"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        pve = None
    return {
        "cpu_vendor": vendor.group(1) if vendor else None,
        "cpu_count": os.cpu_count(),
        "cpu_virt": "vmx" in flags or "svm" in flags,
        "cpu_1g_pages": "pdpe1gb" in flags,
        "iommu_groups": len(groups),
        "cmdline": read("/proc/cmdline", ""),
        "pve_version": pve,
        "efi": os.path.isdir("/sys/firmware/efi"),
    }


def host_use(netdevs, addrs, links):
    used = []
    for n in netdevs:
        link = links.get(n, {})
        if link.get("master"):
            used.append(f"{n} is a member of {link['master']}")
        ips = [a["local"] for a in addrs.get(n, []) if a.get("scope") != "link"]
        if ips:
            used.append(f"{n} has address {', '.join(ips)}")
    return used


def netdevs_of(pci):
    path = os.path.join(SYS_PCI, pci, "net")
    return sorted(os.listdir(path)) if os.path.isdir(path) else []


def device_info(pci, addrs, links):
    base = os.path.join(SYS_PCI, pci)
    if not os.path.isdir(base):
        return {"pci": pci, "exists": False, "driver": None, "netdevs": [], "used_by_host": [],
                "iommu_group": None, "iommu_group_members": []}
    netdevs = netdevs_of(pci)
    group = link_name(os.path.join(base, "iommu_group"))
    members = []
    if group is not None:
        for m in sorted(os.listdir(f"/sys/kernel/iommu_groups/{group}/devices")):
            cls = read(os.path.join(SYS_PCI, m, "class"), "")
            members.append({"pci": m, "class": cls, "bridge": cls.startswith("0x0604"),
                            "driver": link_name(os.path.join(SYS_PCI, m, "driver")),
                            "used_by_host": host_use(netdevs_of(m), addrs, links)})
    return {
        "pci": pci,
        "exists": True,
        "vendor_device": f"{read(base + '/vendor', '')[2:]}:{read(base + '/device', '')[2:]}",
        "driver": link_name(os.path.join(base, "driver")),
        "netdevs": netdevs,
        "carrier": {n: read(f"/sys/class/net/{n}/carrier") for n in netdevs},
        "used_by_host": host_use(netdevs, addrs, links),
        "iommu_group": group,
        "iommu_group_members": members,
        "numa_node": read(base + "/numa_node"),
    }


def judge(host, lab_links, devices):
    problems, warnings = [], []
    if not host["pve_version"]:
        problems.append("This is not a Proxmox VE host (pveversion not found).")
    if not host["cpu_virt"]:
        problems.append("CPU virtualisation (VT-x / AMD-V) is not available: enable it in firmware.")
    if not host["cpu_1g_pages"]:
        problems.append("The CPU lacks 1 GB page support (pdpe1gb), which the VSR needs.")
    if host["iommu_groups"] == 0:
        problems.append("IOMMU is off: enable VT-d / AMD-Vi in firmware.")

    side = {}  # pci -> (link name, "bng" or "gen")
    for link in lab_links:
        side[link["bng_pci"]] = (link["name"], "bng")
        side[link["gen_pci"]] = (link["name"], "gen")

    seen_groups = set()
    for dev in devices:
        name, end = side[dev["pci"]]
        label = f"{name}.{end}_pci {dev['pci']}"
        if not dev["exists"]:
            problems.append(f"{label} does not exist on the host.")
            continue
        for use in dev["used_by_host"]:
            problems.append(f"{label} is in use by the host: {use}.")
        if dev["iommu_group"] is None or dev["iommu_group"] in seen_groups:
            continue
        seen_groups.add(dev["iommu_group"])
        sides = set()
        for m in dev["iommu_group_members"]:
            if m["bridge"]:
                continue
            if m["pci"] in side:
                sides.add(side[m["pci"]][1])
            elif m["used_by_host"]:
                problems.append(f"{label} shares IOMMU group {dev['iommu_group']} with {m['pci']}, "
                                f"which the host uses ({'; '.join(m['used_by_host'])}).")
            else:
                warnings.append(f"{label} shares IOMMU group {dev['iommu_group']} with unused device {m['pci']}; "
                                "it will be passed through along with it.")
        if len(sides) > 1:
            problems.append(f"IOMMU group {dev['iommu_group']} holds both VSR and generator ports; "
                            "a group can only go to one VM. Use ports from a different NIC or slot.")
    return problems, warnings


def main():
    lab_links = json.loads(sys.argv[1])
    pcis = [p for link in lab_links for p in (link["bng_pci"], link["gen_pci"])]
    addrs = {i["ifname"]: i.get("addr_info", []) for i in ip_json("addr", "show")}
    links = {i["ifname"]: i for i in ip_json("link", "show")}
    host = host_info()
    devices = [device_info(p, addrs, links) for p in pcis]
    problems, warnings = judge(host, lab_links, devices)
    json.dump({"host": host, "devices": devices, "problems": problems, "warnings": warnings}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
