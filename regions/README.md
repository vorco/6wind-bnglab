# Region profiles

A region profile describes how one access provider makes its subscribers look
to the BNG: the line identifiers its access network inserts. The lab uses the
profile of each access area's provider to build the generator's subscribers and
the matching RADIUS entries, so a run can simulate, for example, a Chorus
handover next to a Tuatahi one.

## Layout

```
regions/
  README.md
  generic/
    default.yml      # a neutral default, used when an area names no provider
  nz/
    chorus.yml
    enable.yml
    northpower.yml
    tuatahi.yml
```

One directory per country or region, and one file per access provider. An area
names its provider by path, without the extension:

```yaml
lab_access_areas:
  - name: chorus
    s_tag: 2
    provider: nz/chorus
```

## Schema

```yaml
name: Chorus
remote_id:
  - format: "CHORUS{id:010d}B01"
    id_start: 1000000001
circuit_id: "LAB-POLT01 eth 1/1/{s_tag:02d}/{c_tag:02d}/1/1/1:10"
```

| Key | Meaning |
|---|---|
| `name` | The provider's name |
| `remote_id` | One or more remote-id formats. With more than one (for example a current and a legacy form), the provider's subscribers use them in turn, so each type of subscriber (PPPoE and IPoE) gets every format. |
| `remote_id[].format` | The remote-id. Must contain `{id}` |
| `remote_id[].id_start` | The first `{id}`; each format numbers its subscribers from here |
| `circuit_id` | The circuit-id. Optional |

Formats use Python format fields, so `{id:010d}` is the ID zero-padded to ten
digits:

| Field | Value |
|---|---|
| `{id}` | The subscriber's ID within this format (`remote_id` only needs it) |
| `{rsp}` | The retail service provider's code, `lab_rsp_code` (default `RSP`) |
| `{s_tag}`, `{c_tag}` | The subscriber's S-tag and C-tag |
| `{n}` | The subscriber number within its type |

The **remote-id** is the immutable line identifier. The VSR passes it to RADIUS
(`ADSL-Agent-Remote-Id` for PPPoE, `6WIND-dhcp-remote-id` for IPoE), the lab's
RADIUS requires it on every PPPoE subscriber, and IPoE subscribers are
authenticated by it. The **circuit-id** describes where the line attaches. It
is informative only: it is sent, but nothing authenticates on it.

How the generator carries them:

| Subscriber | Remote-id and circuit-id |
|---|---|
| PPPoE | PPPoE intermediate-agent tags |
| IPoE, DHCPv4 | Option 82, sub-options 2 and 1 |
| IPoE, DHCPv6 | Options 37 and 18 (through a lightweight relay agent) |

## Profiles

| Profile | Remote-id | Circuit-id |
|---|---|---|
| `generic/default` | `bnglab-<id>` | `bnglab-olt1 eth 1/<s-tag>/<c-tag>` |
| `nz/chorus` | `CHORUS` + 10-digit line ID + `B01` | `LAB-POLT01 eth 1/1/<s-tag>/<c-tag>/1/1/1:10` |
| `nz/enable` | `EN` + RSP code + `B02` + 6-digit service ID | `ENLABOLT101 xpon 0/1/<s-tag>/<c-tag>:1.101.1.10` |
| `nz/tuatahi` | `UFF` + 12 digits; legacy: `UFF` + RSP code + 2-letter region + 7 digits | `UFFLBOLTLAB001 xpon 0/<s-tag>/0/<c-tag>:1.101.10` |
| `nz/northpower` | `NPF4` + 9 digits + `D` | `NPF_OLT_LAB01 eth 3/1/<s-tag>/<c-tag>/g1:10` |

The New Zealand profiles follow each format as seen by an RSP on that
provider's network; every value in them, including the circuit-ids above, is
invented. Enable only inserts identifiers on Bitstream 2 broadband services
(`B02`); its enterprise products carry none.

## Contributing a region

1. Copy `generic/default.yml` to `<country>/<provider>.yml`.
2. Fill it in with **invented** identifiers that follow the real format. Never
   use real line, circuit or provider codes (use `{rsp}` for your RSP code).
3. Open a pull request saying where the format comes from.

See [CONTRIBUTING.md](../CONTRIBUTING.md).
