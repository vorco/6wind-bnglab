#!/usr/bin/env python3
"""Captures one subscriber's traffic on the VSR and copies the pcap back.

Usage: vsr_capture.py <host> <user> <key-file> <name.pcap> <interface> <filter> <stop-file> <out-file>
       (the VSR's root password in the VSR_ROOT_PASSWORD environment variable)
An empty <key-file> uses the SSH agent and the default keys (~/.ssh/id_*).

Runs `cmd traffic-capture new name <name> filter "<filter>" <interface>` in a VSR CLI
session, writing <out-file>.started once the capture is listening (and failing if it never
starts). When <stop-file> exists it interrupts the capture (as Ctrl-C would), so the capture
is closed cleanly, and copies it from /var/lib/yams/traffic-captures over SFTP as root (the
admin user's login is the CLI, which can't transfer files). Prints a JSON summary. Retries
connections, since the controller may reach the lab over a VPN.
"""
import json
import os
import sys
import time

import paramiko

CAPTURES = "/var/lib/yams/traffic-captures"


def connect(host, user, key_file=None, password=None):
    # With neither a key file nor a password, fall back to the SSH agent and the default keys.
    search_keys = not key_file and not password
    for attempt in range(6):
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(host, username=user, key_filename=key_file, password=password, timeout=30,
                           banner_timeout=60, auth_timeout=60, allow_agent=search_keys,
                           look_for_keys=search_keys)
            return client
        except (OSError, paramiko.SSHException):
            if attempt == 5:
                raise
            time.sleep(10)


def main():
    host, user, key_file, name, interface, pcap_filter, stop_file, out_file = sys.argv[1:9]
    root_password = os.environ.get("VSR_ROOT_PASSWORD", "")
    client = connect(host, user, key_file=os.path.expanduser(key_file) or None)
    shell = client.invoke_shell(width=200)
    time.sleep(3)  # let the CLI start and print its banner
    shell.send(f'cmd traffic-capture new name {name} filter "{pcap_filter}" {interface}\n')
    output = b""
    deadline = time.time() + 120
    while b"listening on" not in output and time.time() < deadline:
        time.sleep(1)
        while shell.recv_ready():
            output += shell.recv(65536)
    if b"listening on" not in output:
        shell.close()
        client.close()
        sys.exit(f"The capture {name} did not start on {interface}:\n{output.decode(errors='replace')}")
    with open(out_file + ".started", "w"):
        pass  # tells the caller the capture is running
    while not os.path.exists(stop_file):
        time.sleep(1)
        while shell.recv_ready():
            output += shell.recv(65536)
    shell.send("\x03")  # stop the capture, which writes and closes the pcap
    deadline = time.time() + 30
    while time.time() < deadline and b"captured" not in output:
        time.sleep(1)
        while shell.recv_ready():
            output += shell.recv(65536)
    shell.close()
    client.close()
    root = connect(host, "root", password=root_password)
    sftp = root.open_sftp()
    sftp.get(f"{CAPTURES}/{name}", out_file)
    sftp.close()
    root.close()
    text = output.decode(errors="replace")
    captured = next((line.split()[0] for line in text.splitlines() if "packets captured" in line), "?")
    print(json.dumps({"name": name, "packets": captured, "file": out_file, "bytes": os.path.getsize(out_file)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
