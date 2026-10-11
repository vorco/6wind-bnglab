# RADIUS

`make radius` builds a FreeRADIUS server for the lab and loads it with the lab
subscribers. Run it after `make vms`.

```bash
make radius                    # build or update; safe to re-run
make radius REBUILD=radius     # destroy and rebuild the container
```

## What it builds

`bnglab-radius` is a Proxmox **application container**, created from the
official `freeradius/freeradius-server` OCI image (pinned, `lab_radius_image`).
Proxmox VE 9 pulls the image into the template storage and runs it as an
unprivileged LXC whose only process is FreeRADIUS. There is no Debian layer and
no Docker. The container is on the management network at
`lab_mgmt.radius_ip` and has the `bnglab` tag.

Application containers are a technology preview in Proxmox VE 9.

`make radius` stages the lab's files on the Proxmox host
(`/var/lib/bnglab/radius`), pushes any that differ into the container with
`pct push`, and restarts the container when something changed:

| File in the container | Contents |
|---|---|
| `/etc/freeradius/clients.conf` | The VSR (`lab_mgmt.vsr_ip`) and localhost, both with `radius_shared_secret` |
| `/etc/freeradius/mods-config/files/authorize` | One entry per lab subscriber, generated from `lab_subscribers` |
| `/etc/freeradius/dictionary.6wind` | 6WIND's vendor attributes (vendor 7336), included from `/etc/freeradius/dictionary` |
| `/etc/freeradius/bnglab/` | The shared secrets, for `radclient` (self-test and CoA) |
| `/var/log/freeradius/radacct/<NAS address>/detail-<date>` | Accounting records (Start, Interim-Update, Stop) |

Changing `lab_subscribers` or a secret and re-running `make radius` pushes the
new files and restarts FreeRADIUS. The container itself is kept.

The FreeRADIUS version only changes when you set a new `lab_radius_image`, for
example to test the BNG against a major FreeRADIUS release. That needs
`REBUILD=radius`, and accounting records in the old container are lost.

To read accounting, run commands in the container from the Proxmox host:

```bash
pct exec <ct-id> -- ls /var/log/freeradius/radacct/
```

When it finishes, `make radius` runs a self-test against the server: one
subscriber of each profile is accepted, an unknown user is rejected, and an
accounting record is written. The IPoE request carries `6WIND-dhcp-remote-id`,
which proves the 6WIND dictionary is loaded. The self-test uses the localhost
client, so its records go to `radacct/127.0.0.1/` and are kept apart from the
VSR's.

## Subscribers: `lab_subscribers`

RADIUS, the VSR and the traffic generator all build subscriber identities from
the same settings, so they always agree. `{n}` is the subscriber number,
starting at 1. Each subscriber's remote-id comes from the provider of its
access area ([region profiles](../regions/README.md)).

```yaml
lab_subscribers:
  broadband_pppoe:
    count: 10
    username: "pppoe{n}@bnglab.example"
    password: "bnglab"
  broadband_dhcp:
    count: 10
    password: "bnglab"        # User-Password the VSR sends for IPoE subscribers
  rate_limited:               # the subscriber of each type rate-limited at login
    n: 2
    mbps: 50
    coa_mbps: 100             # what make test changes it to by CoA
    burst: 125000
```

| Profile | RADIUS User-Name | Authenticated by |
|---|---|---|
| `broadband-pppoe` | the PPP username | its PPP password, and its remote-id (`ADSL-Agent-Remote-Id` must match its line) |
| `broadband-dhcp` | the remote-id (DHCP option 82, or DHCPv6 option 37) | the fixed IPoE password |
| `enterprise` | none (static, no RADIUS) | none |

PPPoE usernames need a realm containing a dot (`@bnglab.example`, not
`@bnglab`), because FreeRADIUS's default policy rejects anything else.

Every Access-Accept carries:

| Attribute | For | Value |
|---|---|---|
| `6WIND-AVPair = "l3vrf=…"` | every subscriber | its subscriber L3VRF (subscribers take `lab_bng.subscriber_vrfs` in turn) |
| `Framed-IP-Address` | RADIUS-addressed subscribers | from `lab_bng.radius.ipv4` |
| `Delegated-IPv6-Prefix` | RADIUS-addressed subscribers | a /56 from `lab_bng.radius.ipv6_pd` |
| `Framed-Route` | the first RADIUS-addressed subscriber of each type | a /29 from `lab_bng.radius.framed_route`, routed to the subscriber |
| `6WIND-limit` (`in=` and `out=`) | the `rate_limited` subscriber of each type | its limit |
| `Acct-Interim-Interval` | every subscriber | `lab_radius_acct_interim` |

Half the subscribers are RADIUS-addressed and half take their addresses from the
VSR's pools; see [access.md](access.md#addressing-lab_bng).

The remote-id is the immutable line identifier, so it is what proves the VSR
passes the access network's identifiers through: a PPPoE subscriber arriving
without its own remote-id is rejected, and an IPoE subscriber is found by it.
The circuit-id is informative only and is not checked.

These are test identities, so they live in the inventory rather than in the
secrets file.

## Change-of-Authorization and Disconnect

The Proxmox host has a helper, `bnglab-coa`, that sends CoA or Disconnect
requests from the RADIUS container to the VSR's PPP server (`pppoe`, port
`lab_bng.coa_port_pppoe`) or IPoE server (`ipoe`, port `lab_bng.coa_port_ipoe`),
using `radius_coa_secret`:

```bash
ssh root@<proxmox-host> bnglab-coa disconnect pppoe 'User-Name = "pppoe1@bnglab.example"'
```

The VSR's CoA servers, and the test that proves them, are described in
[coa.md](coa.md).

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `lab_radius_image` | `docker.io/freeradius/freeradius-server:3.2.10` | FreeRADIUS OCI image |
| `lab_radius_cores` / `lab_radius_memory_mb` / `lab_radius_disk_gb` | `1` / `512` / `2` | Container size |
| `lab_radius_storage` | `""` (`local-zfs`, then `local-lvm`) | Storage for the container's disk |
| `lab_radius_acct_interim` | `300` | Acct-Interim-Interval returned to the VSR, and the VSR's own interim interval, in seconds |
