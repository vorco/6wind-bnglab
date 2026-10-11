# Testing

Two runs check a lab once it is built (`make generator` and the stages before it):

| Command | What it does | Takes |
|---|---|---|
| `make test` | The functional suite: one pass or fail per test, plus evidence pcaps | About 15 minutes |
| `make perf` | A short load run: fast-path core load and how RSS spreads traffic over the RX queues | About 3 minutes of traffic |

Both apply the whole lab configuration first (RADIUS, then the VSR's access, core and
enterprise parts), so they can be run straight after a change. Results go to
`results/test-<time>/` and `results/perf-<time>/`, which are not committed.

## make test

```bash
make test
```

BNG Blaster brings every lab subscriber up (10 PPPoE and 10 IPoE by default, spread over
the access areas' providers and the two subscriber L3VRFs), runs traffic through the core,
and then holds the sessions up while the checks run. The result is
`results/test-<time>/summary.md`: one line per test with a short reason, and the VSR and
BNG Blaster versions. `raw.json`, with everything the checks read, is kept only when a
test fails.

A failing test doesn't stop the run. If some subscribers don't come up complete, test 1
fails and the sessions that did come up are still held for the other tests, so one run
shows every problem in a release. `make perf` likewise runs with what came up and says at
the top of its report how many subscribers were incomplete.

| # | Test | Passes when |
|---|---|---|
| 1 | Sessions up | Every subscriber comes up dual-stack with a delegated prefix (PPPoE: IPCP, IPv6CP and DHCPv6 prefix delegation; IPoE: DHCPv4, DHCPv6 IA_NA and IA_PD), and the VSR has one PPPoE session per PPPoE subscriber and two (IPv4 and IPv6) per IPoE subscriber |
| 2 | Line identifiers | Every PPPoE subscriber was accepted only with its own remote-id (RADIUS requires it) and every IPoE session carries its subscriber's remote-id |
| 3 | RADIUS addressing | Every RADIUS-addressed subscriber has exactly its `Framed-IP-Address` and `Delegated-IPv6-Prefix`, and traffic to and from an address inside each `Framed-Route` subnet passes |
| 4 | Pool addressing | Every pool-addressed subscriber's IPv4 address, IPv6 address or prefix and delegated prefix come from the `lab_bng` pools, and every subscriber's WAN IPv6 comes from the pools |
| 5 | L3VRF placement | Every session is in the L3VRF RADIUS named, and each L3VRF's IPv4 and IPv6 tables hold its own subscribers' addresses, delegated prefixes and routed subnets and none of the other L3VRF's |
| 6 | Traffic | Every flow is verified without loss: IPv4 and IPv6 for PPPoE, IPv4, IPv6 and delegated-prefix IPv6 for IPoE, the routed subnets, both directions, through the labelled core, in both L3VRFs, and for the enterprise customers |
| 7 | Rate limit at login | The rate-limited subscribers show the limit RADIUS gave them; traffic 20% over it arrives at about the limit, and traffic 20% under it arrives intact, in both directions |
| 8 | CoA | A CoA (once for PPPoE, once per IPoE session) is acknowledged, the new limit shows on every session, and traffic 10% over it arrives at about the new limit, in both directions |
| 9 | Disconnect | A Disconnect (once for PPPoE, once per IPoE session) is acknowledged, every session of the disconnected subscribers ends, every other session stays up, and BNG Blaster sees the PPPoE session end |
| 10 | Accounting | After a known transfer, each of the disconnected subscribers' Stop records accounts about the bytes of IP its session carried, in each direction |
| 11 | Evidence pcaps | The pcaps show each subscriber's setup, at least one DHCP renewal and the teardown |

A subscriber's rate limit covers its IPv4 and IPv6 traffic together: one PPPoE session
carries both, and an IPoE subscriber's two sessions are aggregated
(`aggregated-sessions 2`), so the rate checks split their load between the two families.
A DHCP client isn't told when the VSR ends its session, so a disconnected IPoE subscriber
can come back as new sessions when it next renews; the Disconnect check reports that but
judges only the sessions it ended.

### Evidence pcaps

After the checks, the VSR captures two subscribers through a fresh run of every subscriber:
setup, at least one DHCP renewal (the lab's leases are short), a few seconds of traffic
through the core, and the teardown. Each capture is taken on the access LAG with
`cmd traffic-capture`, filtered on the subscriber's MAC address, and copied from the VSR's
`/var/lib/yams/traffic-captures` over SFTP as root (the lab VSR permits root login).

| File | Contents |
|---|---|
| `pcaps/pppoe-dualstack.pcap` | One PPPoE subscriber: discovery with the intermediate-agent tags, LCP, PAP, IPCP, IPv6CP, router advertisements, DHCPv6 prefix delegation and its renewals, traffic, and the PADT |
| `pcaps/ipoe-dhcpv4.pcap` | One IPoE subscriber's IPv4 side: DHCPv4 with option 82, renewals, ARP, traffic and the release |
| `pcaps/ipoe-dhcpv6.pcap` | The same subscriber's IPv6 side: router solicitations and advertisements, DHCPv6 through the lightweight relay agent (options 37 and 18), renewals, traffic and the release |

Packets the VSR sends can appear twice in a capture, once before the NIC fills in their
checksum (shown as a bad UDP checksum) and once after.

### Settings: `lab_test`, and the rate-limited subscriber

```yaml
lab_subscribers:
  rate_limited:          # in subscribers.yml
    n: 2                 # the subscriber of each type RADIUS rate-limits at login
    mbps: 50             # its limit, both directions
    coa_mbps: 100        # the limit make test changes it to by CoA
    burst: 125000        # committed burst size, in bytes
lab_test:                # in test.yml
  accounting:
    n: 3                 # the subscriber of each type whose accounting is checked, then disconnected
    mbps: 20             # the transfer's rate, each direction
    seconds: 20          # and duration
  rate_seconds: 10       # traffic at each rate-limit check
  rate_tolerance: 0.1    # how close the received rate must be to the limit
  accounting_tolerance: 0.05
```

Role defaults: `lab_test_pcap_renew_seconds` (`75`, how long the captured subscribers run
before their traffic, so they renew), `lab_test_pcap_traffic_seconds` (`5`) and
`lab_test_vsr_root_password` (`6windos`, 6WIND's documented default, to copy the pcaps). The VSR's DHCP leases
are `lab_vsr_dhcp_lease` (`120`), `lab_vsr_dhcpv6_preferred` (`60`) and
`lab_vsr_dhcpv6_valid` (`120`) seconds.

## make perf

```bash
make perf                                   # 1 Gbps for 3 minutes across the lab subscribers
make perf PERF_BPS=5000000000 PERF_PPPOE=500 PERF_IPOE=500 PERF_SECONDS=120
```

| Setting | Variable | Default |
|---|---|---|
| `PERF_BPS` | `lab_perf_bps` | `1000000000` (total, both directions, IP layer) |
| `PERF_PPPOE`, `PERF_IPOE` | `lab_perf_pppoe`, `lab_perf_ipoe` | `lab_subscribers`' counts |
| `PERF_SECONDS` | `lab_perf_seconds` | `180` |
| | `lab_perf_packet_bytes` | `1000` (IP packet length) |
| | `lab_perf_sample_seconds` | `5` (how often to sample the cores) |

Every regular stream (each PPPoE subscriber's IPv4 and IPv6, each IPoE subscriber's
IPv4, IPv6 and delegated-prefix IPv6, both directions, plus the routed subnets and
enterprise customers) runs at the same rate, the
total spread over all of them. `results/perf-<time>/perf.md` reports, without pass or fail:

- the offered and received rate and loss, per direction;
- each fast-path core's busy percentage (mean and maximum over the run);
- for each port, the packets each RX queue received and an imbalance figure (busiest
  queue / mean queue, 1.00 being perfectly even), which shows how well RSS spreads the
  subscribers' traffic, including the labelled traffic on the core port.

The per-queue counters come from `show interface hardware-statistics` (`fpn.rxqN_pkts`);
their names can differ with the NIC driver.
