# Customer and region profiles

Details: [access](access.md), [enterprise customers](enterprise.md),
[RADIUS](radius.md) and [region profiles](../regions/README.md).

A test run combines a **customer profile** with a **region profile**:

- The customer profile sets *what* the customer is: how it connects, how it
  gets addresses, and whether RADIUS is involved.
- The region profile sets *what the access network makes it look like*: VLAN
  tagging conventions and the line identifiers inserted by the access provider.

## Customer profiles

Broadband profiles use QinQ (an S-tag per area, a C-tag per subscriber). Each
enterprise customer has its own S-tag, plus a C-tag for a tagged handoff.

| Profile | Access | Addressing | RADIUS |
|---|---|---|---|
| `broadband-pppoe` | PPPoE session | Dual stack: IPv4 from a VSR pool or RADIUS (`Framed-IP-Address`); IPv6 via IPv6CP and SLAAC; a delegated prefix (DHCPv6-PD) from a pool or RADIUS | PPP username and password |
| `broadband-dhcp` | IPoE session | Dual stack: DHCPv4 (address from a VSR pool or RADIUS); DHCPv6 IA_NA, and IA_PD from a pool or RADIUS | Line identifier (remote-id) |
| `enterprise` | Static VLAN interface on its own S-tag (S-tag + C-tag for a tagged handoff), no subscriber session | Static IPv4 and IPv6 on the link, plus a routed IPv6 prefix | None |

## Region profiles

A region profile describes one access provider: the remote-id and circuit-id
its access network inserts (as PPPoE intermediate-agent tags, DHCPv4 option 82
and DHCPv6 options 37 and 18). Each access area names its provider.

| Region | Providers |
|---|---|
| `generic` | A neutral default |
| `nz` | Chorus, Enable, Tuatahi (current and legacy identifiers), Northpower |

Other regions are welcome. The schema and contribution guide are in
[regions/README.md](../regions/README.md).
