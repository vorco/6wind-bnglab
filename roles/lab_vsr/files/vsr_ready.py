#!/usr/bin/env python3
"""Waits until the VSR's fast path is running and its link interfaces are up.

Usage: vsr_ready.py <host> <user> <timeout> <settle> <interface>...   (password on stdin)

Changing fast-path limits or ports restarts the fast path, which is usually
running again within about a minute. The restart may begin 10 to 20 seconds
after the commit returns, so the VSR only counts as ready once every check has
passed continuously for <settle> seconds. Prints a JSON summary; exits 1 on
timeout.

One NETCONF session is kept open and reused, and only reopened when it fails,
so a controller on a VPN or WAN link (where new connections can be slow or
dropped) opens as few connections as possible.
"""
import json
import sys
import time
import xml.etree.ElementTree as ET

from ncclient import manager

POLL = 5
FP = "urn:6wind:vrouter/fast-path"
IF = "urn:6wind:vrouter/interface"
FILTER = ('<state xmlns="urn:6wind:vrouter">'
          '<system xmlns="urn:6wind:vrouter/system">'
          f'<fast-path xmlns="{FP}"><cpu-usage/><limits><fp-cur-if/></limits></fast-path></system>'
          f'<vrf><name>main</name><interface xmlns="{IF}">'
          '<physical><name/><oper-status/></physical></interface></vrf></state>')


class Vsr:
    """A reusable NETCONF session to the VSR, reopened on failure."""

    def __init__(self, host: str, user: str, password: str):
        self.args = dict(host=host, port=830, username=user, password=password, hostkey_verify=False,
                         allow_agent=False, look_for_keys=False, timeout=30, device_params={"name": "default"})
        self.session = None

    def get(self) -> ET.Element:
        if self.session is None or not self.session.connected:
            self.session = manager.connect(**self.args)
        try:
            return ET.fromstring(self.session.get(filter=("subtree", FILTER)).data_xml)
        except Exception:
            self.close()
            raise

    def close(self):
        if self.session is not None:
            try:
                self.session.close_session()
            except Exception:  # already gone
                pass
            self.session = None


def check(vsr: Vsr, interfaces: list[str]) -> list[str]:
    """Returns why the VSR is not ready yet (an empty list means ready)."""
    try:
        root = vsr.get()
    except Exception as e:  # the VSR or the path to it may drop NETCONF
        return [f"netconf: {type(e).__name__}"]
    problems = []
    # cpu-usage and the limits counters are only published while the fast path runs.
    if root.find(f".//{{{FP}}}cpu-usage") is None or root.find(f".//{{{FP}}}limits") is None:
        problems.append("fast path not running")
    status = {p.findtext(f"{{{IF}}}name"): p.findtext(f"{{{IF}}}oper-status")
              for p in root.iter(f"{{{IF}}}physical")}
    problems += [f"{i} {status.get(i) or 'missing'}" for i in interfaces if (status.get(i) or "").upper() != "UP"]
    return problems


def main():
    host, user, timeout, settle, interfaces = (sys.argv[1], sys.argv[2], int(sys.argv[3]),
                                               int(sys.argv[4]), sys.argv[5:])
    vsr = Vsr(host, user, sys.stdin.readline().rstrip("\n"))
    start = time.monotonic()
    ready_since = None
    last = None
    transitions = []
    while True:
        now = time.monotonic()
        problems = check(vsr, interfaces)
        state = ", ".join(problems) or "ready"
        if state != last:
            transitions.append({"at": round(now - start), "state": state})
            last = state
        if problems:
            ready_since = None
        elif ready_since is None:
            ready_since = now
        if ready_since is not None and now - ready_since >= settle:
            vsr.close()
            json.dump({"ready": True, "seconds": round(now - start), "transitions": transitions}, sys.stdout)
            return 0
        if now - start >= timeout:
            vsr.close()
            json.dump({"ready": False, "seconds": round(now - start), "transitions": transitions}, sys.stdout)
            return 1
        time.sleep(POLL)


if __name__ == "__main__":
    sys.exit(main())
