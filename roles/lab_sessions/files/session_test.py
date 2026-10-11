#!/usr/bin/env python3
"""Brings up every lab subscriber with BNG Blaster, checks them, and tears them down.

Usage: session_test.py <config.json> <work-dir> <expected-sessions> <timeout-seconds> [<traffic-seconds>] [--hold]
       session_test.py --stop <work-dir>
       session_test.py --streams <work-dir> <name-prefix> <seconds>
       session_test.py --sessions <work-dir>
       session_test.py --session-streams <work-dir> <seconds> <session-id>...
       session_test.py --wait-core <work-dir>
Waits until every session is established, then reads each one from BNG Blaster
and checks it has an IPv4 address, an IPv6 address or prefix, and a delegated
IPv6 prefix.
With traffic-seconds, it then waits for BGP and LDP to come up, runs every stream for
that long, and requires every flow in both directions to be verified without loss.
Prints a JSON summary; exit code 0 when everything passes.
With --hold, BNG Blaster keeps running afterwards whenever it brought sessions up, even if
some are incomplete, so the next checks still run and report what fails; --stop tears
them down afterwards. --streams runs the held run's streams whose names start with a
prefix for some seconds and prints what each sent and received in that time. Streams
named test-* only run through --streams.
--sessions prints every session of a held run with its identity and addresses.
--session-streams waits for the core, then runs the given sessions' streams (except test-*)
for some seconds.
"""
import json
import os
import subprocess
import sys
import time
from collections import Counter


def stop(work):
    """Tears down a held BNG Blaster run and waits for it to exit. After its sessions are
    gone it still closes BGP and LDP, which can take a minute or two."""
    sock = os.path.join(work, "access.sock")
    if os.path.exists(sock):
        subprocess.run(["bngblaster-cli", sock, "terminate"], capture_output=True, timeout=30)
    for _ in range(300):
        if subprocess.run(["pgrep", "-x", "bngblaster"], capture_output=True).returncode != 0:
            return 0
        time.sleep(1)
    subprocess.run(["pkill", "-x", "bngblaster"])
    return 1


def run_streams(work, prefix, seconds):
    """Runs the streams whose names start with prefix and prints their counters' growth."""
    sock = os.path.join(work, "access.sock")

    def summary():
        out = subprocess.run(["bngblaster-cli", sock, "stream-summary"], capture_output=True, text=True, timeout=60)
        return [f for f in json.loads(out.stdout or "{}").get("stream-summary", [])
                if f.get("name", "").startswith(prefix)]

    # Streams through the core need BGP up first.
    for _ in range(60):
        out = subprocess.run(["bngblaster-cli", sock, "bgp-sessions"], capture_output=True, text=True, timeout=30)
        bgp = json.loads(out.stdout or "{}").get("bgp-sessions", [])
        if all(b.get("state") == "established" and b.get("raw-update-state") == "done" for b in bgp):
            break
        time.sleep(2)
    before = {f["flow-id"]: f for f in summary()}
    names = sorted({f["name"] for f in before.values()})
    for name in names:
        subprocess.run(["bngblaster-cli", sock, "stream-start", "name", name], capture_output=True, timeout=30)
    started = time.time()
    time.sleep(seconds)
    for name in names:
        subprocess.run(["bngblaster-cli", sock, "stream-stop", "name", name], capture_output=True, timeout=30)
    elapsed = time.time() - started  # how long the streams really ran, for rates
    time.sleep(3)  # let the last packets arrive
    flows = []
    for f in summary():
        b = before.get(f["flow-id"], {})
        flows.append({k: f.get(k) for k in ("name", "direction", "session-id")}
                     | {k: f.get(k, 0) - b.get(k, 0) for k in ("tx-packets", "rx-packets", "tx-bytes", "rx-bytes")})
    print(json.dumps({"seconds": round(elapsed, 2), "flows": flows}))
    return 0 if flows else 1


def dump_sessions(work):
    """Prints every session of a held run: its identity, tags and addresses."""
    sock = os.path.join(work, "access.sock")
    keys = ("session-id", "type", "username", "agent-remote-id", "outer-vlan", "inner-vlan", "session-state",
            "ipv4-address", "ipv6-address", "ipv6-prefix", "ipv6-delegated-prefix", "mac", "client-mac")
    sessions, session_id = [], 1
    while True:
        out = subprocess.run(["bngblaster-cli", sock, "session-info", "session-id", str(session_id)],
                             capture_output=True, text=True, timeout=30)
        info = json.loads(out.stdout or "{}").get("session-info")
        if not info:
            break
        sessions.append({k: info.get(k) for k in keys})
        session_id += 1
    print(json.dumps(sessions))
    return 0 if sessions else 1


def session_streams(work, seconds, session_ids):
    """Runs only the given sessions' streams (except test-*) for some seconds, once BGP and
    LDP are up."""
    sock = os.path.join(work, "access.sock")

    def cli(*args):
        out = subprocess.run(["bngblaster-cli", sock, *args], capture_output=True, text=True, timeout=30)
        return json.loads(out.stdout or "{}")

    for _ in range(60):
        bgp = cli("bgp-sessions").get("bgp-sessions", [])
        if bgp and all(b.get("state") == "established" and b.get("raw-update-state") == "done" for b in bgp):
            break
        time.sleep(2)
    flows = [f for f in cli("stream-summary").get("stream-summary", [])
             if f.get("session-id") in session_ids and not f.get("name", "").startswith("test-")]
    for f in flows:
        cli("stream-start", "flow-id", str(f["flow-id"]))
    time.sleep(seconds)
    for f in flows:
        cli("stream-stop", "flow-id", str(f["flow-id"]))
    print(json.dumps({"flows": len(flows)}))
    return 0 if flows else 1


def wait_core(work):
    """Waits (up to two minutes) for a held run's BGP and LDP sessions and their updates."""
    sock = os.path.join(work, "access.sock")
    for _ in range(60):
        state = {}
        for kind, ok in (("bgp-sessions", ("established", "done")), ("ldp-sessions", ("operational", "done"))):
            out = subprocess.run(["bngblaster-cli", sock, kind], capture_output=True, text=True, timeout=30)
            sessions = json.loads(out.stdout or "{}").get(kind, [])
            state[kind] = bool(sessions) and all((x.get("state"), x.get("raw-update-state")) == ok for x in sessions)
        if all(state.values()):
            print(json.dumps(state))
            return 0
        time.sleep(2)
    print(json.dumps(state))
    return 1


if sys.argv[1] == "--wait-core":
    sys.exit(wait_core(sys.argv[2]))
if sys.argv[1] == "--session-streams":
    sys.exit(session_streams(sys.argv[2], int(sys.argv[3]), [int(i) for i in sys.argv[4:]]))
if sys.argv[1] == "--sessions":
    sys.exit(dump_sessions(sys.argv[2]))
if sys.argv[1] == "--stop":
    sys.exit(stop(sys.argv[2]))
if sys.argv[1] == "--streams":
    sys.exit(run_streams(sys.argv[2], sys.argv[3], int(sys.argv[4])))

hold = "--hold" in sys.argv
args = [a for a in sys.argv[1:] if a != "--hold"]
config, work, expected, timeout = args[0], args[1], int(args[2]), int(args[3])
traffic_seconds = int(args[4]) if len(args) > 4 else 0
sock, log = os.path.join(work, "access.sock"), os.path.join(work, "access.log")
if os.path.exists(sock):
    os.remove(sock)


def cli(*args):
    out = subprocess.run(["bngblaster-cli", sock, *args], capture_output=True, text=True, timeout=30)
    try:
        return json.loads(out.stdout or "{}")
    except ValueError:
        return {}


def complete(s):
    return bool(s.get("session-state") == "Established" and s.get("ipv4-address")
                and (s.get("ipv6-address") or s.get("ipv6-prefix"))
                and s.get("ipv6-delegated-prefix"))


def read_sessions():
    return [cli("session-info", "session-id", str(i)).get("session-info", {}) for i in range(1, expected + 1)]


# A held run must outlive this script (and the SSH session that started it).
blaster = subprocess.Popen(["bngblaster", "-C", config, "-S", sock, "-L", log, "-l", "info"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=hold)
counters, sessions, started = {}, [], time.time()
core, flows = {}, []


def wait_for_core(limit=120):
    """Waits for BGP established and LDP operational, with their RAW updates sent."""
    deadline = time.time() + limit
    state = {}
    while time.time() < deadline:
        bgp = cli("bgp-sessions").get("bgp-sessions", [])
        ldp = cli("ldp-sessions").get("ldp-sessions", [])
        state = {"bgp": [(b.get("state"), b.get("raw-update-state")) for b in bgp],
                 "ldp": [(l.get("state"), l.get("raw-update-state")) for l in ldp]}
        if bgp and ldp and all(b == ("established", "done") for b in state["bgp"]) \
                and all(l == ("operational", "done") for l in state["ldp"]):
            time.sleep(5)  # let the VSR install the routes it just learned
            break
        time.sleep(2)
    return state
try:
    # Wait for every session to establish, then for every one to finish IPv6 and prefix delegation.
    while time.time() - started < timeout and blaster.poll() is None:
        time.sleep(5)
        if not os.path.exists(sock):
            continue
        counters = cli("session-counters").get("session-counters", counters)
        if counters.get("sessions-established", 0) >= expected:
            sessions = read_sessions()
            if all(complete(s) for s in sessions):
                break
    if blaster.poll() is None and not sessions:
        sessions = read_sessions()
    established_after = round(time.time() - started)
    if traffic_seconds and blaster.poll() is None:
        core = wait_for_core()
        # Every stream except make test's own (test-*), which only run through --streams.
        names = sorted({f["name"] for f in cli("stream-summary").get("stream-summary", [])
                        if not f.get("name", "").startswith("test-")})
        for name in names:
            cli("stream-start", "name", name)
        time.sleep(traffic_seconds)
        for name in names:
            cli("stream-stop", "name", name)
        time.sleep(3)  # let the last packets arrive
        flows = [f for f in cli("stream-summary").get("stream-summary", [])
                 if not f.get("name", "").startswith("test-")]
finally:
    failed = [s for s in sessions if not complete(s)]
    sessions_ok = bool(sessions) and not failed and len(sessions) == expected
    # Held even when some sessions are incomplete: make test judges them and runs its other
    # checks, rather than stopping at the first problem.
    held = hold and bool(sessions) and blaster.poll() is None
    if blaster.poll() is None and not held:
        cli("terminate")
        try:
            blaster.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            blaster.kill()

traffic = {}
for f in flows:
    t = traffic.setdefault(f"{f.get('name')} {f.get('direction')}", {"flows": 0, "verified": 0, "tx": 0, "rx": 0})
    t["flows"] += 1
    t["verified"] += 1 if f.get("verified") else 0
    t["tx"] += f.get("tx-packets", 0)
    t["rx"] += f.get("rx-packets", 0)
traffic_ok = all(t["verified"] == t["flows"] and t["rx"] >= t["tx"] for t in traffic.values())
# The first few flows that failed, with their session, so a failure can be traced on the VSR.
unverified = [{k: f.get(k) for k in ("name", "direction", "session-id", "flow-id", "tx-packets", "rx-packets")}
              for f in flows if not f.get("verified") or f.get("rx-packets", 0) < f.get("tx-packets", 0)][:10]
summary = {
    "expected": expected,
    "complete": len(sessions) - len(failed),
    "with_delegated_prefix": Counter(s.get("type") for s in sessions if s.get("ipv6-delegated-prefix")),
    "seconds": established_after,
    "counters": {k: v for k, v in counters.items() if "established" in k or k in ("sessions", "sessions-flapped")},
    "failed_states": Counter(f"{s.get('type')}: {s.get('session-state')} {s.get('session-substate', '')}"
                             f" dhcp={s.get('dhcp-state')} dhcpv6={s.get('dhcpv6-state')}" for s in failed),
    "core": core,
    "traffic": traffic,
    "unverified_flows": unverified,
    "held": held,
    "examples": [{k: v for k, v in s.items()
                  if k in ("type", "username", "agent-remote-id", "outer-vlan", "inner-vlan", "ipv4-address",
                           "ipv6-prefix", "ipv6-address", "ipv6-delegated-prefix")}
                 for s in ([sessions[0], sessions[-1]] if sessions else [])],
}
print(json.dumps(summary))
sys.exit(0 if sessions_ok and (not traffic_seconds or (traffic and traffic_ok)) else 1)
