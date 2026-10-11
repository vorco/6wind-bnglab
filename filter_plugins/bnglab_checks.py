"""make test's checks: each takes the data gathered from BNG Blaster, the VSR and RADIUS
and returns {"id", "name", "passed", "detail"} for the run's summary."""

import ipaddress
import json
import re


def result(test_id, name, problems, ok_detail):
    """A check result: passed when there are no problems; the detail names the first few."""
    if problems:
        more = f" (and {len(problems) - 3} more)" if len(problems) > 3 else ""
        return {"id": test_id, "name": name, "passed": False, "detail": "; ".join(problems[:3]) + more}
    return {"id": test_id, "name": name, "passed": True, "detail": ok_detail}


def find_session(plan_entry, sessions):
    """The BNG Blaster session of a planned subscriber (by PPP username or remote-id)."""
    key = "username" if plan_entry["type"] == "pppoe" else "agent-remote-id"
    want = plan_entry["username"] if plan_entry["type"] == "pppoe" else plan_entry["remote_id"]
    return next((s for s in sessions if s.get("type") == plan_entry["type"] and s.get(key) == want), None)


def check_sessions(plan, summary, vsr_pppoe, vsr_ipoe):
    pppoe = [p for p in plan if p["type"] == "pppoe"]
    ipoe = [p for p in plan if p["type"] == "ipoe"]
    problems = []
    if summary.get("complete") != summary.get("expected"):
        problems.append(f"{summary.get('expected', 0) - summary.get('complete', 0)} of {summary.get('expected')} "
                        f"subscribers did not come up dual-stack with a delegated prefix: {dict(summary.get('failed_states', {}))}")
    if len(vsr_pppoe) != len(pppoe):
        problems.append(f"the VSR has {len(vsr_pppoe)} PPPoE sessions, expected {len(pppoe)}")
    if len(vsr_ipoe) != 2 * len(ipoe):
        problems.append(f"the VSR has {len(vsr_ipoe)} IPoE sessions, expected {2 * len(ipoe)} (one per address family)")
    return result(1, "Sessions up", problems,
                  f"{len(pppoe)} PPPoE and {len(ipoe)} IPoE subscribers up dual-stack with a delegated prefix; "
                  f"the VSR has {len(vsr_pppoe)} PPPoE and {len(vsr_ipoe)} IPoE sessions")


def check_identifiers(plan, vsr_ipoe):
    problems = []
    ipoe = [p for p in plan if p["type"] == "ipoe"]
    for p in ipoe:
        mine = [s for s in vsr_ipoe if s.get("username") == p["username"]]
        if not mine or any(s.get("remote-id") != p["remote_id"] for s in mine):
            problems.append(f"IPoE {p['remote_id']}: the VSR shows remote-id "
                            f"{sorted({s.get('remote-id') for s in mine}) or 'no session'}")
    providers = sorted({p["provider"] for p in plan})
    return result(2, "Line identifiers", problems,
                  f"every PPPoE subscriber was accepted only with its own remote-id, and every IPoE session "
                  f"carries its subscriber's remote-id ({', '.join(providers)})")


def check_radius_addressing(plan, sessions, traffic):
    problems, routed = [], 0
    for p in (p for p in plan if p["addressing"] == "radius"):
        s = find_session(p, sessions) or {}
        if s.get("ipv4-address") != p["framed_ip"]:
            problems.append(f"{p['username']}: IPv4 {s.get('ipv4-address')}, RADIUS gave {p['framed_ip']}")
        if s.get("ipv6-delegated-prefix") != p["delegated_prefix"]:
            problems.append(f"{p['username']}: delegated prefix {s.get('ipv6-delegated-prefix')}, "
                            f"RADIUS gave {p['delegated_prefix']}")
        routed += bool(p["framed_route"])
    routed_flows = {k: t for k, t in traffic.items() if "-routed-" in k}
    if routed and len(routed_flows) < 2 * routed:
        problems.append("no traffic was run to the Framed-Route subnets")
    for name, t in routed_flows.items():
        if t["verified"] != t["flows"] or t["rx"] < t["tx"]:
            problems.append(f"Framed-Route traffic {name}: {t['verified']}/{t['flows']} verified, {t['rx']}/{t['tx']} received")
    count = sum(1 for p in plan if p["addressing"] == "radius")
    return result(3, "RADIUS addressing", problems,
                  f"{count} subscribers got exactly their Framed-IP-Address and Delegated-IPv6-Prefix; traffic to "
                  f"and from {routed} Framed-Route subnets passed")


def in_range(address, pool):
    """Whether an address is in a pool given as a network or as first-last."""
    if not address:
        return False
    if "-" in pool:
        first, last = (ipaddress.ip_address(a) for a in pool.split("-"))
        return first <= ipaddress.ip_address(address.split("/")[0]) <= last
    return ipaddress.ip_network(address, strict=False).subnet_of(ipaddress.ip_network(pool))


def check_pool_addressing(plan, sessions, bng):
    problems = []
    pool = [p for p in plan if p["addressing"] == "pool"]
    for p in pool:
        s = find_session(p, sessions) or {}
        pools = bng["pppoe" if p["type"] == "pppoe" else "ipoe"]
        v6 = s.get("ipv6-prefix")  # PPPoE: the SLAAC /64; IPoE: the IA_NA address, as a /128
        for what, value, where in (("IPv4", s.get("ipv4-address"), pools["ipv4_pool"]),
                                   ("IPv6", v6, pools["ipv6_pool"]),
                                   ("delegated prefix", s.get("ipv6-delegated-prefix"), pools["ipv6_pd"])):
            if not in_range(value, where):
                problems.append(f"{p['username']}: {what} {value} is not from {where}")
    radius = [p for p in plan if p["addressing"] == "radius"]
    for p in radius:  # RADIUS-addressed subscribers still take their WAN IPv6 from the pools
        s = find_session(p, sessions) or {}
        pools = bng["pppoe" if p["type"] == "pppoe" else "ipoe"]
        v6 = s.get("ipv6-prefix")  # PPPoE: the SLAAC /64; IPoE: the IA_NA address, as a /128
        if not in_range(v6, pools["ipv6_pool"]):
            problems.append(f"{p['username']}: IPv6 {v6} is not from {pools['ipv6_pool']}")
    return result(4, "Pool addressing", problems,
                  f"{len(pool)} subscribers got IPv4, IPv6 and a delegated prefix from the VSR's pools, and every "
                  f"subscriber's WAN IPv6 came from the pools")


def check_vrfs(plan, sessions, vsr_pppoe, vsr_ipoe, routes):
    """routes: {vrf: {"ipv4": [destinations], "ipv6": [destinations]}}."""
    problems = []
    for p in plan:
        mine = [s for s in (vsr_pppoe if p["type"] == "pppoe" else vsr_ipoe) if s.get("username") == p["username"]]
        if not mine or any(s.get("l3vrf") != p["vrf"] for s in mine):
            problems.append(f"{p['username']}: in {sorted({s.get('l3vrf') for s in mine}) or 'no session'}, "
                            f"RADIUS named {p['vrf']}")
        s = find_session(p, sessions) or {}
        wanted = [("ipv4", f"{s.get('ipv4-address')}/32"), ("ipv6", s.get("ipv6-delegated-prefix"))]
        if p["framed_route"]:
            wanted.append(("ipv4", p["framed_route"]))
        for family, destination in wanted:
            for vrf, table in routes.items():
                present = destination in table.get(family, [])
                if vrf == p["vrf"] and not present:
                    problems.append(f"{p['username']}: {destination} missing from {vrf}")
                elif vrf != p["vrf"] and present:
                    problems.append(f"{p['username']}: {destination} also in {vrf}")
    counts = ", ".join(f"{vrf}: {sum(1 for p in plan if p['vrf'] == vrf)} subscribers" for vrf in routes)
    return result(5, "L3VRF placement", problems,
                  f"every session is in the L3VRF RADIUS named, and each L3VRF's IPv4 and IPv6 tables hold "
                  f"exactly its own subscribers' addresses, delegated prefixes and routed subnets ({counts})")


def check_traffic(summary):
    traffic, problems = summary.get("traffic", {}), []
    if not traffic:
        problems.append(f"no traffic ran (core: {summary.get('core')})")
    for name, t in sorted(traffic.items()):
        if t["verified"] != t["flows"] or t["rx"] < t["tx"]:
            problems.append(f"{name}: {t['verified']}/{t['flows']} flows verified, {t['rx']}/{t['tx']} packets received")
    flows = sum(t["flows"] for t in traffic.values())
    return result(6, "Traffic", problems,
                  f"all {flows} flows verified without loss: IPv4, IPv6 and delegated-prefix IPv6, both "
                  f"directions, through the labelled core, both L3VRFs and the enterprise customers")


def rate_report(run, limit_mbps):
    """Per direction: offered and received Mbps (IP layer, 1000-byte packets) and the ratio."""
    seconds, out = run["seconds"], {}
    for direction in ("upstream", "downstream"):
        flows = [f for f in run["flows"] if f["direction"] == direction]
        tx = sum(f["tx-packets"] for f in flows)
        rx = sum(f["rx-packets"] for f in flows)
        out[direction] = {"offered": tx * 8000 / seconds / 1e6, "received": rx * 8000 / seconds / 1e6,
                          "ratio": rx / tx if tx else 0.0}
    return out


def check_rate(test_id, name, runs, tolerance):
    """runs: [{"label", "limit", "offered", "run"}]. A run offered above its limit must
    arrive at about the limit; one offered below must arrive intact."""
    problems, notes = [], []
    for r in runs:
        for direction, m in rate_report(r["run"], r["limit"]).items():
            share = "upload" if direction == "upstream" else "download"
            if r["offered"] > r["limit"]:
                expected = r["limit"] / r["offered"]
                ok = abs(m["ratio"] - expected) <= tolerance * expected
                want = f"about {r['limit']} Mbps"
            else:
                ok = m["ratio"] >= 0.98
                want = f"all {r['offered']:.0f} Mbps"
            notes.append(f"{r['label']} {share} {m['offered']:.0f}->{m['received']:.0f} Mbps")
            if not ok:
                problems.append(f"{r['label']} {share}: offered {m['offered']:.1f} Mbps, received "
                                f"{m['received']:.1f} Mbps, expected {want}")
    return result(test_id, name, problems, "; ".join(notes))


def check_limits(test_id, name, sessions, usernames, limit):
    """Every session of the given subscribers shows the limit in the VSR's table."""
    problems, mine = [], [s for s in sessions if s.get("username") in usernames]
    if not mine:
        problems.append(f"no sessions for {', '.join(usernames)} on the VSR")
    for s in mine:
        for d in ("in", "out"):
            if not (s.get(f"rate-limit-{d}") or "").startswith(f"{limit} "):
                problems.append(f"{s.get('username')} {s.get('interface')}: rate-limit-{d} is "
                                f"{s.get(f'rate-limit-{d}') or 'none'}, expected {limit} Mbps")
    return result(test_id, name, problems, f"{len(mine)} sessions limited to {limit} Mbps both ways")


def parse_detail(text):
    """FreeRADIUS detail-file records as dicts (attribute values unquoted, hex decoded)."""
    out = []
    for block in re.split(r"\n\s*\n", text):
        record = {}
        for line in block.splitlines():
            m = re.match(r"^\s+([\w-]+) = (.*)$", line)
            if m:
                value = m.group(2).strip().strip('"')
                if re.fullmatch(r"0x[0-9a-fA-F]+", value):
                    try:
                        value = bytes.fromhex(value[2:]).decode()
                    except (ValueError, UnicodeDecodeError):
                        pass
                record[m.group(1)] = value
        if record:
            out.append(record)
    return out


def accounting_sessions(vsr_pppoe, vsr_ipoe, pppoe_user, ipoe_user):
    """The accounted sessions of the accounting subscribers: {acct_session_id: {label, type, families}}.
    A PPPoE session carries both families; each IPoE session one (IPv4 if it has an IPv4 address)."""
    out = {}
    for s in vsr_pppoe:
        if s.get("username") == pppoe_user:
            out[s.get("accounting-session-id")] = {"label": "PPPoE", "type": "pppoe", "families": ["ipv4", "ipv6"]}
    for s in vsr_ipoe:
        if s.get("username") == ipoe_user:
            v4 = "." in (s.get("ip") or "")
            out[s.get("accounting-session-id")] = {"label": "IPoE " + ("IPv4" if v4 else "IPv6"), "type": "ipoe",
                                                   "families": ["ipv4"] if v4 else ["ipv6"]}
    return out


def check_disconnect(test_id, before, after, users, established, expected, requests):
    """before/after: the VSR's sessions (PPPoE and IPoE together) before and after the
    Disconnects; users: the disconnected subscribers. Every one of their sessions must have
    ended and every other session must still be there; BNG Blaster must see exactly one session
    end (a PPPoE client is told; a DHCP client isn't). A subscriber whose DHCP client renews
    afterwards comes back as new sessions, which are reported, not failed."""
    key = "accounting-session-id"
    targeted = {s[key] for s in before if s.get("username") in users}
    remaining = {s[key] for s in after}
    problems = [f"session {i} of a disconnected subscriber is still up" for i in sorted(targeted & remaining)]
    missing = {s[key] for s in before if s.get("username") not in users} - remaining
    problems += [f"{len(missing)} other sessions also went"] if missing else []
    if int(established) != int(expected) - 1:
        problems.append(f"BNG Blaster has {established} sessions established, expected {int(expected) - 1}")
    back = [s for s in after if s.get("username") in users and s[key] not in targeted]
    note = (f"; {len(back)} new sessions came back afterwards (the DHCP client renewed)" if back else "")
    return result(test_id, "Disconnect", problems,
                  f"Disconnect-ACK for all {requests} requests; {len(targeted)} sessions of {', '.join(users)} "
                  f"ended and every other session stayed up{note}")


def session_stops(detail_text):
    """{Acct-Session-Id: the Stop record that follows that session's latest Start}. The VSR
    can reuse an Acct-Session-Id after a restart, so older records with the same ID are
    ignored."""
    stops, started = {}, set()
    for record in parse_detail(detail_text):
        acct_id, kind = record.get("Acct-Session-Id"), record.get("Acct-Status-Type")
        if kind == "Start":
            started.add(acct_id)
            stops.pop(acct_id, None)
        elif kind == "Stop" and acct_id in started:
            stops[acct_id] = record
            started.discard(acct_id)
    return stops


def check_accounting(test_id, detail_text, sessions, volume, tolerance):
    """sessions: accounting_sessions(); volume: a --streams run of the accounting subscribers.
    Each session's Stop record must account about the IP bytes its streams carried."""
    stops = session_stops(detail_text)
    problems, notes = [], []
    if not sessions:
        problems.append("the accounting subscribers had no sessions on the VSR")
    for acct_id, info in sessions.items():
        record = stops.get(acct_id)
        if not record:
            problems.append(f"{info['label']}: no Accounting-Request Stop for session {acct_id}")
            continue
        flows = [f for f in volume["flows"] if any(f"-{info['type']}-{fam}-" in f["name"] for fam in info["families"])]
        sent = sum(f["tx-packets"] for f in flows if f["direction"] == "upstream") * 1000
        received = sum(f["rx-packets"] for f in flows if f["direction"] == "downstream") * 1000
        octets_in = int(record.get("Acct-Input-Octets", 0)) + (int(record.get("Acct-Input-Gigawords", 0)) << 32)
        octets_out = int(record.get("Acct-Output-Octets", 0)) + (int(record.get("Acct-Output-Gigawords", 0)) << 32)
        for what, accounted, actual in (("upload", octets_in, sent), ("download", octets_out, received)):
            notes.append(f"{info['label']} {what} {accounted / 1e6:.1f} MB accounted for {actual / 1e6:.1f} MB")
            if not actual or abs(accounted - actual) > tolerance * actual:
                problems.append(f"{info['label']} {what}: accounted {accounted} octets, the subscriber "
                                f"{'sent' if what == 'upload' else 'received'} {actual} bytes of IP")
    return result(test_id, "Accounting", problems, "; ".join(notes))


def route_tables(results):
    """vsr_query routes results (an Ansible loop over [vrf, family]) as {vrf: {family: [destinations]}}."""
    tables = {}
    for r in results:
        vrf, family = r["item"]
        tables.setdefault(vrf, {})[family] = [route.get("destination") for route in json.loads(r["stdout"])]
    return tables


RATE_LOADS = (("PPPoE (IPv4 + IPv6)", "-pppoe-"), ("IPoE (IPv4 + IPv6)", "-ipoe-"))


def rate_runs(stdout, label, limit, offered, seconds):
    """A session_test.py --streams run of the rate-limited subscribers as check_rate runs, one
    per subscriber: its limit covers its IPv4 and IPv6 traffic together (one PPPoE session;
    an IPoE subscriber's aggregated sessions)."""
    run = json.loads(stdout)
    return [{"label": f"{name} {label}".strip(), "limit": int(limit), "offered": float(offered),
             "run": {"seconds": run.get("seconds") or seconds, "flows": [f for f in run["flows"] if match in f["name"]]}}
            for name, match in RATE_LOADS]


def session_subject(plan, kind, n):
    """A subscriber's BNG Blaster session ID and MAC address. BNG Blaster numbers sessions in
    the order of its access blocks (one per subscriber, in plan order) and gives session i the
    MAC 02:00:00:00:<i, two bytes>."""
    for index, p in enumerate(plan):
        if p["type"] == kind and p["n"] == int(n):
            sid = index + 1
            return {"session_id": sid, "mac": f"02:00:00:00:{sid >> 8:02x}:{sid & 0xFF:02x}",
                    "username": p["username"]}
    raise ValueError(f"no {kind} subscriber {n}")


EVIDENCE = {
    "pppoe-dualstack": ["PADI", "PADO", "PADR", "PADS", "LCP", "PAP", "IPCP", "IPv6CP", "DHCPv6 Solicit",
                        "DHCPv6 Reply", "DHCPv6 Renew", "PADT"],
    "ipoe-dhcpv4": ["DHCP Discover", "DHCP Offer", "DHCP Request", "DHCP Ack", "DHCP Release"],
    "ipoe-dhcpv6": ["Router Advertisement", "DHCPv6 Solicit", "DHCPv6 Advertise", "DHCPv6 Request",
                    "DHCPv6 Reply", "DHCPv6 Renew", "DHCPv6 Release"],
}


def check_pcaps(test_id, captures):
    """captures: {name: pcap_check.py result}. Each pcap must show its subscriber's setup,
    at least one renewal and the teardown (the PADT, or the DHCP and DHCPv6 releases)."""
    problems, notes = [], []
    for name, wanted in EVIDENCE.items():
        found = captures.get(name, {}).get("messages", {})
        missing = [m for m in wanted if not found.get(m)]
        if name == "ipoe-dhcpv4" and found.get("DHCP Request", 0) < 2:
            missing.append("a renewal (second DHCP Request)")
        if missing:
            problems.append(f"{name}.pcap lacks {', '.join(missing)}")
        notes.append(f"{name}.pcap: {captures.get(name, {}).get('packets', 0)} packets, "
                     + ", ".join(f"{m} x{c}" for m, c in sorted(found.items())))
    return result(test_id, "Evidence pcaps", problems, "; ".join(notes))


def merge_results(results, test_id, name, lead=""):
    """One result from several: passed when all passed; the detail is the failures' or, when
    everything passed, the lead followed by every detail."""
    failed = [r["detail"] for r in results if not r["passed"]]
    if failed:
        return {"id": test_id, "name": name, "passed": False, "detail": "; ".join(failed)}
    return {"id": test_id, "name": name, "passed": True,
            "detail": "; ".join(([lead] if lead else []) + [r["detail"] for r in results])}


def rx_queues(cli_output):
    """{queue: packets} from `show interface hardware-statistics` (fpn.rxqN_pkts)."""
    return {int(m.group(1)): int(m.group(2)) for m in re.finditer(r"fpn\.rxq(\d+)_pkts:\s*(\d+)", cli_output)}


def perf_report(cpu, queues_before, queues_after, run, settings):
    """make perf's report as Markdown. cpu: vsr_query cpu output; queues_*: {interface: cli
    output}; run: a session_test.py --streams run; settings: what was asked for."""
    seconds = run.get("seconds") or settings["seconds"]
    lines = [f"# make perf: {settings['stamp']}", "",
             f"VSR {settings['vsr_version']} · {settings['pppoe']} PPPoE and {settings['ipoe']} IPoE subscribers · "
             f"{settings['flows']} flows of {settings['packet_bytes']}-byte packets · "
             f"{settings['bps'] / 1e9:g} Gbps offered for {seconds:.0f} s", ""]
    if int(settings.get("complete", 0)) != int(settings.get("expected", 0)):
        # Incomplete subscribers still carry the traffic they can (for example IPv4), so the
        # run goes ahead, but the report says so.
        lines += [f"**{int(settings['expected']) - int(settings['complete'])} of {settings['expected']} subscribers "
                  f"did not come up dual-stack with a delegated prefix:** {dict(settings.get('failed_states') or {})}", ""]
    lines += ["## Throughput", "",
             "| Direction | Offered Mbps | Received Mbps | Loss |", "|---|---|---|---|"]
    for direction in ("upstream", "downstream"):
        flows = [f for f in run["flows"] if f["direction"] == direction]
        tx, rx = sum(f["tx-packets"] for f in flows), sum(f["rx-packets"] for f in flows)
        mbps = settings["packet_bytes"] * 8 / seconds / 1e6
        loss = f"{(tx - rx) / tx:.2%}" if tx else "-"
        lines.append(f"| {direction} | {tx * mbps:.0f} | {rx * mbps:.0f} | {loss} |")
    lines += ["", "## Fast-path cores", "", "| Core | Busy % (mean) | Busy % (max) | Samples |", "|---|---|---|---|"]
    for c in cpu:
        values = c["samples"] or [0]
        lines.append(f"| {c['cpu']} | {sum(values) / len(values):.0f} | {max(values)} | {len(c['samples'])} |")
    lines += ["", "## RX queues", "",
              "Packets each port received per RX queue during the run, and how evenly RSS spread them "
              "(imbalance: busiest queue / mean queue; 1.00 is perfectly even).", ""]
    for interface in sorted(queues_after):
        before, after = rx_queues(queues_before.get(interface, "")), rx_queues(queues_after[interface])
        delta = {q: after[q] - before.get(q, 0) for q in sorted(after)}
        total = sum(delta.values())
        mean = total / len(delta) if delta else 0
        lines += [f"**{interface}**: {total} packets, imbalance "
                  f"{(max(delta.values()) / mean if mean else 0):.2f}", "",
                  "| Queue | Packets | Share |", "|---|---|---|"]
        lines += [f"| {q} | {n} | {n / total:.1%} |" if total else f"| {q} | {n} | - |" for q, n in delta.items()]
        lines.append("")
    return "\n".join(lines) + "\n"


class FilterModule:
    def filters(self):
        return {
            "bnglab_check_sessions": check_sessions,
            "bnglab_check_identifiers": check_identifiers,
            "bnglab_check_radius_addressing": check_radius_addressing,
            "bnglab_check_pool_addressing": check_pool_addressing,
            "bnglab_check_vrfs": check_vrfs,
            "bnglab_check_traffic": check_traffic,
            "bnglab_check_rate": check_rate,
            "bnglab_check_limits": check_limits,
            "bnglab_check_accounting": check_accounting,
            "bnglab_accounting_sessions": accounting_sessions,
            "bnglab_parse_detail": parse_detail,
            "bnglab_session_stops": session_stops,
            "bnglab_check_disconnect": check_disconnect,
            "bnglab_merge_results": merge_results,
            "bnglab_session_subject": session_subject,
            "bnglab_check_pcaps": check_pcaps,
            "bnglab_perf_report": perf_report,
            "bnglab_route_tables": route_tables,
            "bnglab_rate_runs": rate_runs,
        }
