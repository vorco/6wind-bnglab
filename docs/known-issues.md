# Known issues

Findings from running the lab that other operators are likely to hit. Each
entry says what happens, how the lab deals with it, and which versions it was
seen on.

## FreeRADIUS 3.2.5 and later require Message-Authenticator

**Seen on:** FreeRADIUS 3.2.5 and later.

Since the BlastRADIUS hardening, FreeRADIUS requires a Message-Authenticator
attribute from clients defined in `clients.conf`
(`require_message_authenticator = yes`). It drops other requests **without
logging anything** unless it runs in debug mode, so sessions fail at
authentication with no clue in the log.

Clients loaded from SQL (`read_clients = yes`) are created with the check
turned off. That is why an existing SQL-backed deployment can work while a new
`clients.conf` setup does not.

**In the lab:** the VSR client in `clients.conf` has
`require_message_authenticator = no`, which is safe on the lab's isolated
management network. The localhost client keeps the default. If your BNG sends
Message-Authenticator, you can keep the default for it too.

## Size the VSR's memory beyond its hugepages

The fast path sizes its memory from its configured limits, which default to
production scale (a million IPv4 and IPv6 routes, and more). Give the VSR
enough memory beyond its hugepages for the control plane as well. The lab
defaults to 16 GB (`lab_vms_vsr_memory_gb`).

## A fast-path restart starts after the commit returns

**Seen on:** VSR 3.11.10.

Changing the fast-path ports or limits restarts the fast path, but not straight
away. The commit returns and the VSR still looks ready; 10 to 20 seconds later
the fast path stops, its ports disappear and come back down, and it is running
again about 40 to 50 seconds after the commit. NETCONF stays up throughout. A
test started straight after the commit fails for reasons unrelated to the
change being tested.

**In the lab:** after any change, `lab_vsr` waits until the fast path is running
and every link interface has stayed up for 45 seconds (see
[access](access.md#what-is-configured-on-the-vsr-over-netconf)).

## A controller on a VPN can see new connections dropped

**Seen on:** a controller reaching the lab over a site-to-site VPN.

Ping across the VPN was clean, but new TCP connections to every lab address
(the Proxmox host, the VSR's SSH and NETCONF, the generator) failed
intermittently for tens of seconds at a time, while the same connections from
the Proxmox host never failed. Runs then failed with NETCONF timeouts or SSH
errors that had nothing to do with the VSR.

**In the lab:** Ansible retries SSH and reuses connections, NETCONF has generous
connect and command timeouts (`ansible.cfg`), and the readiness wait keeps one
NETCONF session open instead of reconnecting. A NETCONF connection that can't
be set up at all still fails the run, because Ansible treats it as the VSR
being unreachable rather than retrying. If that keeps happening, check the VPN
or firewall for limits on new connections, or run the controller on the lab's
management network (for example a small VM on the Proxmox host).

## BNG Blaster applies a stream's receive label to both directions

**Seen on:** BNG Blaster `dev` (2026-10).

On a bidirectional stream, `rx-label1` is checked on the downstream packets as
well as the upstream ones. Downstream packets reach the subscriber unlabelled,
so they never verify.

**In the lab:** every core stream is defined once per direction (see
[core](core.md#what-bng-blaster-does)).
