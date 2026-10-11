#!/usr/bin/env python3
"""Reads the VSR's subscriber sessions or an L3VRF's routes over NETCONF, as JSON.

Usage: vsr_query.py <host> <user> sessions pppoe|ipoe [<username>]   (password on stdin)
       vsr_query.py <host> <user> routes ipv4|ipv6 <l3vrf>
       vsr_query.py <host> <user> version
       vsr_query.py <host> <user> cpu <seconds> <interval>
sessions prints [{"username": ..., "status": ..., "l3vrf": ..., "rate-limit-in": ..., ...}]
from the VSR's own session table (show-ppp-server-session / show-ipoe-server-session, with
details), optionally only one username's. routes prints [{"destination": ..., "protocol": ...}]
for every route in the L3VRF (show-ipv4-routes / show-ipv6-routes). version prints the
VSR's product version. cpu samples each fast-path core's busy percentage every <interval>
seconds for <seconds> and prints [{"cpu": ..., "samples": [...]}].
"""
import json
import sys
import time
import xml.etree.ElementTree as ET

from ncclient import manager
from ncclient.xml_ import to_ele

SESSIONS = {
    "pppoe": ("urn:6wind:vrouter/ppp-server", "show-ppp-server-session", "<instance>bnglab</instance>"),
    "ipoe": ("urn:6wind:vrouter/ipoe-server", "show-ipoe-server-session", ""),
}
ROUTING = "urn:6wind:vrouter/routing"


def run_long_command(m, rpc, ns):
    """Runs a long-running show command: its first reply carries a status RPC, which is
    polled until the command exits, each poll returning the next chunk of results."""
    reply = m.dispatch(to_ele(rpc))
    status = ET.fromstring(reply.xml).findtext(f"{{{ns}}}status-rpc")
    replies = [reply]
    for _ in range(120):
        if not status or ET.fromstring(replies[-1].xml).find(f"{{{ns}}}exit-code") is not None:
            break
        time.sleep(0.5)
        replies.append(m.dispatch(to_ele(status)))
    return replies


def records(reply, ns, tag):
    """Each <tag> element of the reply as a dict; repeated children become lists."""
    out = []
    for element in ET.fromstring(reply.xml).iter(f"{{{ns}}}{tag}"):
        fields = {}
        for child in element:
            key = child.tag.split("}")[1]
            if len(child):
                continue  # nested detail (next hops) isn't needed
            if key in fields or key == "vlans":
                fields[key] = (fields.get(key) if isinstance(fields.get(key), list)
                               else ([fields[key]] if key in fields else [])) + [child.text]
            else:
                fields[key] = child.text or ""
        out.append(fields)
    return out


def main():
    host, user, what = sys.argv[1], sys.argv[2], sys.argv[3]
    password = sys.stdin.readline().rstrip("\n")
    if what == "cpu":
        seconds, interval = int(sys.argv[4]), int(sys.argv[5])
        fp = "urn:6wind:vrouter/fast-path"
        state = (f'<state xmlns="urn:6wind:vrouter"><system xmlns="urn:6wind:vrouter/system">'
                 f'<fast-path xmlns="{fp}"><cpu-usage/></fast-path></system></state>')
        samples = {}
        with manager.connect(host=host, port=830, username=user, password=password, hostkey_verify=False,
                             allow_agent=False, look_for_keys=False, timeout=60,
                             device_params={"name": "default"}) as m:
            end = time.time() + seconds
            while time.time() < end:
                root = ET.fromstring(m.get(filter=("subtree", state)).data_xml)
                for usage in root.iter(f"{{{fp}}}cpu-usage"):
                    samples.setdefault(usage.findtext(f"{{{fp}}}cpu"), []).append(int(usage.findtext(f"{{{fp}}}busy") or 0))
                time.sleep(interval)
        json.dump([{"cpu": cpu, "samples": values} for cpu, values in sorted(samples.items())], sys.stdout)
        return
    if what == "version":
        state = '<state xmlns="urn:6wind:vrouter"><system xmlns="urn:6wind:vrouter/system"><product/></system></state>'
        with manager.connect(host=host, port=830, username=user, password=password, hostkey_verify=False,
                             allow_agent=False, look_for_keys=False, timeout=60,
                             device_params={"name": "default"}) as m:
            root = ET.fromstring(m.get(filter=("subtree", state)).data_xml)
        # The product container's namespace differs between releases, so match on local names.
        json.dump(next((c.text for p in root.iter() if p.tag.endswith("}product")
                        for c in p if c.tag.endswith("}version")), ""), sys.stdout)
        return
    if what == "sessions":
        kind = sys.argv[4]
        ns, name, extra = SESSIONS[kind]
        match = f"<match><username>{sys.argv[5]}</username></match>" if len(sys.argv) > 5 else ""
        rpc = f'<{name} xmlns="{ns}"><vrf>main</vrf>{extra}<details/>{match}</{name}>'
        tag = "session"
    else:
        family, l3vrf = sys.argv[4], sys.argv[5]
        ns, name = ROUTING, f"show-{family}-routes"
        rpc = f'<{name} xmlns="{ns}"><vrf>main</vrf><l3vrf>{l3vrf}</l3vrf></{name}>'
        tag = "route"
    with manager.connect(host=host, port=830, username=user, password=password, hostkey_verify=False,
                         allow_agent=False, look_for_keys=False, timeout=60,
                         device_params={"name": "default"}) as m:
        replies = [m.dispatch(to_ele(rpc))] if what == "sessions" else run_long_command(m, rpc, ns)
    json.dump([r for reply in replies for r in records(reply, ns, tag)], sys.stdout)


if __name__ == "__main__":
    main()
