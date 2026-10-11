#!/usr/bin/env python3
"""Runs VSR CLI commands over SSH and prints their output.

Usage: vsr_cli.py <host> <user> <key-file> <command>...
An empty <key-file> uses the SSH agent and the default keys (~/.ssh/id_*).
Each argument is one CLI command (for example "show interface hardware-statistics name
core"). The VSR's admin login is its CLI, which reads commands from standard input.
Retries the connection, since the controller may reach the lab over a VPN.
"""
import os
import sys
import time

import paramiko


def main():
    host, user, key_file, commands = sys.argv[1], sys.argv[2], os.path.expanduser(sys.argv[3]) or None, sys.argv[4:]
    for attempt in range(6):
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(host, username=user, key_filename=key_file, timeout=30, banner_timeout=60,
                           auth_timeout=60, allow_agent=not key_file, look_for_keys=not key_file)
            break
        except (OSError, paramiko.SSHException):
            if attempt == 5:
                raise
            time.sleep(10)
    # A shell session without a terminal: the CLI reads the commands and exits at end of input.
    channel = client.get_transport().open_session(timeout=60)
    channel.settimeout(120)
    channel.invoke_shell()
    channel.sendall("".join(f"{c}\n" for c in commands).encode())
    channel.shutdown_write()
    output = b""
    while True:
        data = channel.recv(65536)
        if not data:
            break
        output += data
    sys.stdout.write(output.decode(errors="replace"))
    client.close()


if __name__ == "__main__":
    main()
