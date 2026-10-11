# Inventory

An Ansible inventory and a secrets file describe your lab. The repository
ships examples of both; your real copies are never committed.

| Path | Contents | Committed? |
|---|---|---|
| `inventory/example/` | A complete example using documentation addresses | yes |
| `inventory/local/` | Your copy of the example, edited for your host | no (gitignored) |
| `vars/secrets.example.yml` | The secrets the lab needs, with placeholders | yes |
| `vars/secrets.yml` | Your licence serial, passwords and keys | no (gitignored) |

## Getting started

```bash
cp -r inventory/example inventory/local
cp vars/secrets.example.yml vars/secrets.yml
# edit both, then:
make check-inventory
```

`make check-inventory` validates everything below on your workstation without
contacting the host. To use other paths, pass `INVENTORY=<dir>` or
`SECRETS=<file>` to any `make` target.

## Host

`inventory/local/hosts.yml` holds exactly one Proxmox VE host in the `pve`
group. Ansible connects to it as root over SSH, so set up key-based login first
(`ssh-copy-id root@<host>`).

```yaml
all:
  children:
    pve:
      hosts:
        lab-pve:
          ansible_host: 192.0.2.10
          ansible_user: root
```

## Management network: `lab_mgmt`

The VSR, the generator and the RADIUS container each get one address on a
Proxmox bridge. The VSR activates its licence online over this network, so the
gateway and DNS must give it internet access.

| Key | Example | Meaning |
|---|---|---|
| `bridge` | `vmbr0` | Proxmox bridge the guests attach to |
| `prefix` | `24` | Prefix length of the management subnet |
| `gateway` | `192.0.2.1` | Default gateway for the guests |
| `dns` | `[192.0.2.53]` | Resolvers for the guests |
| `ntp` | `[pool.ntp.org]` | NTP servers for the guests |
| `timezone` | `Etc/UTC` | Guest time zone (tz database name) |
| `vsr_ip` | `192.0.2.20` | VSR management address (NETCONF, SSH) |
| `gen_ip` | `192.0.2.21` | Generator management address (SSH) |
| `radius_ip` | `192.0.2.22` | FreeRADIUS container address |

The three guest addresses must be distinct, inside the subnet, and must not be
the gateway.

## Lab guests

The lab creates three guests: `bnglab-vsr`, `bnglab-gen` (VMs) and
`bnglab-radius` (LXC). Proxmox allocates their IDs, and each guest carries the
Proxmox tag `bnglab`. Reruns and teardown find the guests by name and tag, and
never touch anything without that tag.

## Looped links: `lab_links`

There is one entry per physical cable looped between two NIC ports on the host.
`bng_pci` is passed through to the VSR and `gen_pci` to the generator.

```yaml
lab_links:
  - name: access1
    role: access          # subscriber-facing
    tpid: "0x8100"        # S-tag ethertype on this port
    bng_pci: "0000:01:00.0"
    gen_pci: "0000:02:00.0"
  - name: core
    role: core            # network-facing
    bng_pci: "0000:01:00.1"
    gen_pci: "0000:02:00.1"
```

| Key | Meaning |
|---|---|
| `name` | Unique label, used in reports and VSR descriptions. Up to 11 characters: lower-case letters, digits and hyphens, starting with a letter. It becomes an interface name on the generator and `<name>-lag` on the VSR, and interface names are limited to 15 characters |
| `role` | `access` (one or more links) or `core` (exactly one link) |
| `bng_pci` | Full PCI address of the port given to the VSR, lower case |
| `gen_pci` | Full PCI address of the port given to the generator, lower case |
| `tpid` | Access links only: the ethertype of every S-tag on the port, `"0x8100"` (default) or `"0x88a8"` |

Find PCI addresses on the host with `lspci -D | grep -i ethernet`. Every address
must be distinct. `make preflight` also checks on the host that each pair is
really cabled together.

The TPID belongs to the physical port: every S-tag on an access link uses the
same ethertype, as on a real handover. Intel 700-series NICs (X710, XL710,
XXV710) go further and do RSS for only one TPID per chip, so if two VSR access
ports on the same chip use different TPIDs, one of them gets no RSS.
`make check-inventory` warns when that happens.

Several access links let one run simulate several handovers. Each access
area (S-tag) names its access provider, so one link can also carry several
providers' subscribers in a single run.

## Secrets: `vars/secrets.yml`

| Key | Meaning |
|---|---|
| `vsr_license_serial` | 6WIND licence serial, activated online at first boot |
| `vsr_admin_user` | Admin account cloud-init creates on the VSR (NETCONF and SSH) |
| `vsr_admin_password` | Password for that account |
| `lab_ssh_public_keys` | Public keys allowed to log in to the VSR and the generator |
| `radius_shared_secret` | Shared secret between the VSR and FreeRADIUS |
| `radius_coa_secret` | Shared secret for CoA and Disconnect messages to the VSR |

`make check-inventory` rejects any `CHANGE-ME` or the example key.

`make test` and `make perf` log in to the VSR's admin account with the private
key that matches one of `lab_ssh_public_keys`. By default they use the Proxmox
host's `ansible_ssh_private_key_file`, or, if that isn't set, your SSH agent and your
default keys (`~/.ssh/id_*`). To use a particular key, set `lab_ssh_private_key_file` in
your inventory:

| Variable | Default | Meaning |
|---|---|---|
| `lab_ssh_private_key_file` | the Proxmox host's `ansible_ssh_private_key_file`, or else the SSH agent and default keys | Private key the lab's scripts use to log in to the VSR, for example `~/.ssh/bnglab_ed25519` |

To keep the file encrypted at rest:

```bash
ansible-vault encrypt vars/secrets.yml
make check-inventory VAULT=1     # prompts for the vault password
```

## Images: `vsr_image`

The VSR image comes from your 6WIND support portal as a `.qcow2` file. Keep it
on your workstation, anywhere outside git, and point `vsr_image` at it:

```yaml
vsr_image: images/6wind-vsr-x86_64-<version>.qcow2   # relative to the repo root, or absolute
vsr_image_sha256: "<sha256>"                          # optional, checked before upload
```

`images/` in the repository is gitignored, if you want to keep images there.

`make images` uploads the VSR image to the host's import area (`local:import/`)
and downloads the Ubuntu 24.04 cloud image for the generator directly on the
host. Ubuntu's image is checked against Ubuntu's published `SHA256SUMS`.
Files that are already there and identical are not copied again.

Both images go into the storage's **import** content type, which Proxmox
provides for disk images to be imported into VMs. Ubuntu names its cloud image
`.img`, but the file is qcow2, and the import content type only accepts files
named by their real format (`.qcow2`, `.raw`, `.vmdk`), so the lab saves it as
`noble-server-cloudimg-amd64.qcow2`. The Proxmox admin guide's cloud-init
example instead imports the `.img` by absolute path; the lab uses the import
content type so that VMs reference images by volume ID
(`local:import/<file>`) and Proxmox applies its checks for imported images.

Several VSR versions can be staged side by side, which is handy for release
qualification. Stage one per run with `VSR_IMAGE`:

```bash
make images VSR_IMAGE=../6wind-vsr-x86_64-<other-version>.qcow2
```

| Variable | Default | Meaning |
|---|---|---|
| `vsr_image` | none (required) | Path to the VSR `.qcow2` on the workstation |
| `vsr_image_sha256` | unset | Expected sha256 of the image; upper or lower case |
| `images_gen_url` | Ubuntu 24.04 `current` cloud image | Generator image to download |
| `images_gen_sums_url` | Ubuntu 24.04 `current` `SHA256SUMS` | Checksums the download is verified against |

## VMs

`make vms` builds and starts two VMs from the staged images:

| VM | Default size | Ports | First-boot setup |
|---|---|---|---|
| `bnglab-vsr` | 3 cores (1 control plane, 2 fast path), 16 GB | every `bng_pci` | 6WIND Day-1 file: hostname, management VRF, NETCONF and SSH (root login allowed: it's a lab), admin user, licence, fast-path cores |
| `bnglab-gen` | 6 cores, 12 GB | every `gen_pci` | Proxmox cloud-init: user `lab` with your SSH keys and its management address |

Both VMs:
- are q35 machines with `host` CPUs (`pdpe1gb` flag);
- get all their memory from the host's 1 GB hugepages, so `pve_host_hugepages_1g` must cover both (28 by default);
- receive their link ports as whole PCIe devices.

Proxmox allocates their IDs, and each carries the `bnglab` tag.

When the VMs are up, `make vms` waits for the VSR's NETCONF port and the
generator's first boot to finish, then checks that the VSR licence is active.

**VSR management port.** The VSR names ports by their PCI location inside the
VM (`pci-b<bus>s<slot>`), which depends on how many devices the VM has. The
first-boot configuration finds the management port by its MAC address, so no
port name needs to be configured.

**CPU pinning.** By default the first `lab_vms_host_cores` physical cores are
left to Proxmox. The VSR is then pinned to the next cores, and the generator to
the cores after it, each on the NUMA node of its NICs. Set
`lab_vms_vsr_affinity` or `lab_vms_gen_affinity` (for example `"2-4"`) to choose
the cores yourself.

**Rebuilding.** A VM's build settings (image, sizing, ports, network and
first-boot configuration) are recorded in its Proxmox description. If they
change, `make vms` stops and says so rather than destroying anything:

```bash
make vms REBUILD=vsr                                                    # rebuild the VSR only
make vms REBUILD=vsr VSR_IMAGE=../6wind-vsr-x86_64-<other-version>.qcow2   # qualify another release
make vms REBUILD=all                                                    # rebuild both VMs
```

Only VMs named `bnglab-vsr` or `bnglab-gen` with the `bnglab` tag are ever
destroyed. CPU pinning changes are applied without a rebuild.

**Licences.** Every new VSR activates the licence again, and a destroyed VSR's
activation is not handed back. After rebuilding, free the old activation in
6WIND's licensing portal (Deactivate), or the next build may find no tokens
left.

| Variable | Default | Meaning |
|---|---|---|
| `lab_vms_vsr_cores` | `3` | VSR vCPUs: 1 control plane plus fast path (about 1 fast-path core per 5 Gbps) |
| `lab_vms_vsr_memory_gb` | `16` | VSR memory, from hugepages. The fast path reserves much of it for its default limits, so leave room for the control plane (see [known issues](known-issues.md#size-the-vsrs-memory-beyond-its-hugepages)) |
| `lab_vms_vsr_disk_gb` | `32` | VSR disk |
| `lab_vms_gen_cores` | `6` | Generator vCPUs |
| `lab_vms_gen_memory_gb` | `12` | Generator memory, from hugepages |
| `lab_vms_gen_disk_gb` | `32` | Generator disk |
| `lab_vms_host_cores` | `2` | Physical cores left to Proxmox before pinning |
| `lab_vms_vsr_affinity`, `lab_vms_gen_affinity` | `""` (automatic) | Explicit host CPU lists |
| `lab_vms_storage` | `""` (`local-zfs`, then `local-lvm`) | Storage for VM disks |
| `lab_vms_boot_timeout` | `600` | Seconds to wait for the VMs |

## Access areas and addressing: `lab_access_areas`, `lab_bng`

The access network's areas (S-tags, each with its provider's
[region profile](../regions/README.md)) and the subscriber addressing plan are
described in [access.md](access.md). `lab_rsp_code` (default `RSP`) is the
retail service provider code that some providers' identifiers contain.

## Subscribers: `lab_subscribers`

The lab's test subscribers (counts, usernames and passwords)
are described in [radius.md](radius.md#subscribers-lab_subscribers).

## Tests: `lab_test`

The settings for `make test` and `make perf` (`test.yml`) are described in
[testing.md](testing.md).

## Host preparation

The settings for `make host` (hugepages, reserved RAM, repositories and
storage) are listed in [hardware.md](hardware.md#preparing-your-host-make-host).
