# Contributing

Thanks for helping. The aim is a lab that any operator running 6WIND VSR as a
BNG can stand up from a fresh Proxmox host and trust for repeatable results.

## Most wanted: region profiles

Access networks differ in how they tag subscribers and how they identify the
line: S-tag and C-tag conventions, remote-id and circuit-id formats, and
PPPoE intermediate-agent or DHCP relay options. A region profile captures this
so the lab's subscribers look like the ones a production BNG sees.

To add one, follow [regions/README.md](regions/README.md). The schema is
defined there. Please:

- use **made-up identifiers** that follow the real format, never real
  subscriber or circuit IDs;
- name the wholesale provider and cite a public source for the format if one
  exists;
- keep one directory per country or region, with one file per access provider.

## Other changes

1. Open an issue first for anything larger than a fix, so we can agree the shape.
2. Keep changes small: one feature or fix per pull request.
3. Update the docs your change touches in the same pull request.
4. Run `make lint` before opening the pull request.

## What must never be committed

This repository is public. Do not commit:

- licence serials, passwords, password hashes, shared secrets or private keys
  (these belong in `vars/secrets.yml`, which is gitignored);
- your real inventory (keep it in `inventory/local/`, which is gitignored);
- real IP addresses, hostnames, MAC addresses or subscriber identifiers from your
  network. Examples use documentation ranges: `192.0.2.0/24`, `198.51.100.0/24`,
  `203.0.113.0/24` and `2001:db8::/32`;
- 6WIND images, documentation excerpts or YANG models;
- test results (`results/` is gitignored).

## Style

- Ansible: fully qualified module names, `name:` on every task, and idempotent
  tasks. `make lint` runs `yamllint`, `ansible-lint` and a syntax check of every playbook.
- Python: standard library first, and type hints on public functions.
- Markdown: one sentence per idea, and Mermaid for diagrams.
