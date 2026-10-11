#!/usr/bin/env python3
"""Plans CPU pinning for the lab VMs. Prints JSON {"vsr": "2-4", "gen": "5-10"}.

Usage: cpu_plan.py '<JSON request>'
Request: {"host_cores": 2, "vms": [{"name": "vsr", "cores": 3, "pcis": [...]}, ...]}
The first host_cores physical cores are left to Proxmox. Each VM then gets
whole physical cores (one thread each) on the NUMA node of its NICs, in order.
"""
import json
import subprocess
import sys


def topology():
    out = subprocess.run(["lscpu", "-p=CPU,NODE,CORE,SOCKET"], capture_output=True, text=True, check=True).stdout
    cores = {}  # (socket, core) -> {"node": n, "cpus": [...]}
    for line in out.splitlines():
        if line.startswith("#"):
            continue
        cpu, node, core, socket = (int(x) if x else 0 for x in line.split(","))
        entry = cores.setdefault((socket, core), {"node": node, "cpus": []})
        entry["cpus"].append(cpu)
    return sorted(cores.values(), key=lambda c: min(c["cpus"]))


def nic_node(pcis):
    nodes = []
    for pci in pcis:
        try:
            with open(f"/sys/bus/pci/devices/{pci}/numa_node") as f:
                nodes.append(max(int(f.read().strip()), 0))
        except OSError:
            nodes.append(0)
    return max(set(nodes), key=nodes.count) if nodes else 0


def ranges(cpus):
    cpus, parts, start = sorted(cpus), [], None
    for i, c in enumerate(cpus):
        if start is None:
            start = c
        if i + 1 == len(cpus) or cpus[i + 1] != c + 1:
            parts.append(f"{start}-{c}" if c != start else f"{c}")
            start = None
    return ",".join(parts)


def main():
    req = json.loads(sys.argv[1])
    free = topology()[req["host_cores"]:]
    plan, errors = {}, []
    for vm in req["vms"]:
        node = nic_node(vm["pcis"])
        picked = [c for c in free if c["node"] == node][: vm["cores"]]
        if len(picked) < vm["cores"]:
            errors.append(f"{vm['name']} needs {vm['cores']} free physical cores on NUMA node {node}, "
                          f"only {len(picked)} are left")
            continue
        free = [c for c in free if c not in picked]
        plan[vm["name"]] = ranges(min(c["cpus"]) for c in picked)
    json.dump({"plan": plan, "errors": errors}, sys.stdout)


if __name__ == "__main__":
    main()
