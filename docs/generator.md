# Generator

`make generator` prepares the `bnglab-gen` VM to run
[BNG Blaster](https://github.com/rtbrick/bngblaster). Run it after `make vms`.

```bash
make generator
```

## What it does

1. **NIC drivers and firmware.** The Ubuntu cloud image's kernel keeps most
   physical NIC drivers in `linux-modules-extra`, so that package (for the
   running kernel) and `linux-firmware` are installed and the link ports are
   probed.
2. **Link port names.** Each passthrough port is named after its link in
   `lab_links` (for example `access1`, `core`) with a systemd `.link` file, and
   set to MTU `lab_generator_port_mtu` (9216). Proxmox adds the ports to the VM
   in `lab_links` order, so the generator's ports in PCI order match the
   inventory. The VM reboots once to apply the names.
3. **Port preparation.** `bnglab-ports.service` brings the ports up at boot with
   no addresses, IPv6 disabled and GRO/LRO off. BNG Blaster sends and receives
   raw frames on them.
4. **BNG Blaster from source.** libdict comes from rtbrick's release packages
   (checksum-pinned), and BNG Blaster is built from `lab_generator_bngblaster_ref`
   and installed as `/usr/bin/bngblaster`. It is rebuilt only when that ref
   resolves to a different commit.

When it finishes, `make generator` checks that `bngblaster` runs and that every
link port is up at the right MTU, then reports the BNG Blaster commit and the
ports' state.

## BNG Blaster version

The default is a pinned commit on rtbrick's `dev` branch (2026-10-03). It
includes two fixes the lab relies on:

- DHCP replies with padding after the END option, which RFC 2131 permits and
  VSR offers include, are decoded
  ([rtbrick/bngblaster#402](https://github.com/rtbrick/bngblaster/pull/402)).
- Stream traffic is received on LAG member interfaces in RX threads
  ([rtbrick/bngblaster#403](https://github.com/rtbrick/bngblaster/pull/403)).
  This matters for the access LAG, and for jumbo test frames on it.

To follow the branch instead, set `lab_generator_bngblaster_ref: dev`. To use a
release, set its tag.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `lab_generator_bngblaster_ref` | dev commit `cc3e0f9c…` | BNG Blaster commit, branch or tag to build |
| `lab_generator_bngblaster_repo` | `https://github.com/rtbrick/bngblaster.git` | Source repository |
| `lab_generator_libdict_url` / `lab_generator_libdict_sha256` | libdict 1.0.6 for Ubuntu 24.04 | libdict packages and their checksum |
| `lab_generator_port_mtu` | `9216` | MTU of the link ports |
| `lab_generator_src` | `/opt/bngblaster` | Where BNG Blaster is built on the generator |
