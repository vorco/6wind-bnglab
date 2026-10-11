"""Ansible filters shared by the 6wind-bnglab roles."""

import ipaddress
import math
import os

import yaml


REGIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "regions")
DEFAULT_PROVIDER = "generic/default"
C_TAG_FIRST, C_TAG_LAST = 2, 4000


def load_provider(name, regions_dir=REGIONS_DIR):
    """Loads a region profile, regions/<name>.yml, and checks its formats."""
    path = os.path.join(regions_dir, name + ".yml")
    try:
        with open(path) as f:
            profile = yaml.safe_load(f)
    except OSError as err:
        raise ValueError(f"provider {name}: no region profile at regions/{name}.yml") from err
    formats = profile.get("remote_id") or []
    if not formats or not all("{id" in v.get("format", "") for v in formats):
        raise ValueError(f"provider {name}: remote_id needs at least one format containing {{id}}")
    return profile


def subscribers_plan(subscribers, areas, links, rsp="RSP", bng=None, regions_dir=REGIONS_DIR):
    """Places every lab subscriber on the access network and gives it its identities.

    Each profile's subscribers are split evenly across the areas. Within an area
    (one S-tag) PPPoE subscribers take the even C-tags and IPoE subscribers the odd
    ones, from C-tag 2, so both types share every S-tag. Each area's provider
    (a region profile, default generic/default) sets the remote-id and circuit-id
    the access network inserts. A provider with several remote-id formats (such as
    a current and a legacy one) uses them in turn within each type; each format
    numbers its subscribers from its own id_start. Returns one entry per subscriber:

        {n, type, area, provider, link, s_tag, tpid, c_tag, username, remote_id, circuit_id,
         vrf, addressing, framed_ip, delegated_prefix, framed_route, rate_limit_mbps}

    n is the subscriber number within its type ({n} in lab_subscribers). username
    is the PPP username for PPPoE and the remote-id (what the VSR authenticates
    IPoE by) for IPoE.

    With bng (lab_bng), subscribers of each type take the subscriber L3VRFs in turn,
    and alternate in pairs between RADIUS addressing (a Framed-IP-Address and a
    Delegated-IPv6-Prefix from bng.radius) and the VSR's pools ("pool"), so every
    L3VRF has both. The first RADIUS-addressed subscriber of each type also gets a
    Framed-Route. Raises ValueError when the plan doesn't fit.
    """
    tpids = {link["name"]: link.get("tpid", "0x8100") for link in links}
    profiles, counters, plan = {}, {}, []
    per_area = {}
    for kind, key in (("pppoe", "broadband_pppoe"), ("ipoe", "broadband_dhcp")):
        total = int(subscribers[key]["count"])
        size = math.ceil(total / len(areas)) if total else 0
        for index, area in enumerate(areas):
            first = index * size + 1
            per_area[(area["name"], kind)] = range(first, min(first + size, total + 1))
    for area in areas:
        name = area.get("provider", DEFAULT_PROVIDER)
        if name not in profiles:
            profiles[name] = load_provider(name, regions_dir)
        profile = profiles[name]
        link = area.get("link", links[0]["name"])
        # Interleave the two types so the provider's formats alternate across both.
        pppoe, ipoe = per_area[(area["name"], "pppoe")], per_area[(area["name"], "ipoe")]
        order = [("pppoe", i, n) for i, n in enumerate(pppoe)] + [("ipoe", i, n) for i, n in enumerate(ipoe)]
        order.sort(key=lambda o: (o[1], o[0] == "ipoe"))
        for kind, i, n in order:
            c_tag = C_TAG_FIRST + 2 * i + (1 if kind == "ipoe" else 0)
            if c_tag > C_TAG_LAST:
                raise ValueError(f"area {area['name']} needs C-tag {c_tag}; the limit is {C_TAG_LAST} "
                                 f"(1999 subscribers of each type per area)")
            formats = profile["remote_id"]
            # Alternate formats within each type, so PPPoE and IPoE both get every format.
            turn = counters.setdefault((name, kind, "turn"), 0)
            variant = turn % len(formats)
            counters[(name, kind, "turn")] = turn + 1
            seq = counters.setdefault((name, variant), 0)
            counters[(name, variant)] = seq + 1
            fields = {"id": int(formats[variant].get("id_start", 1)) + seq, "rsp": rsp,
                      "s_tag": int(area["s_tag"]), "c_tag": c_tag, "n": n}
            remote_id = formats[variant]["format"].format(**fields)
            username = (subscribers["broadband_pppoe"]["username"].replace("{n}", str(n))
                        if kind == "pppoe" else remote_id)
            plan.append({
                "n": n,
                "type": kind,
                "area": area["name"],
                "provider": name,
                "link": link,
                "s_tag": int(area["s_tag"]),
                "tpid": tpids[link],
                "c_tag": c_tag,
                "username": username,
                "remote_id": remote_id,
                "circuit_id": profile.get("circuit_id", "").format(**fields),
            })
    if bng:
        assign_service(plan, subscribers, bng)
    ids = [p["remote_id"] for p in plan]
    if len(set(ids)) != len(ids):
        raise ValueError("two subscribers got the same remote-id; give the providers' formats distinct id_start values")
    return plan


def assign_service(plan, subscribers, bng):
    """Adds each subscriber's L3VRF, addressing and login rate limit (see subscribers_plan)."""
    vrfs = [v["name"] for v in bng["subscriber_vrfs"]]
    radius = bng.get("radius", {})
    ipv4 = ipaddress.IPv4Network(radius["ipv4"]) if radius.get("ipv4") else None
    pds = ipaddress.IPv6Network(radius["ipv6_pd"]).subnets(new_prefix=56) if radius.get("ipv6_pd") else None
    routes = ipaddress.IPv4Network(radius["framed_route"]).subnets(new_prefix=29) if radius.get("framed_route") else None
    limited = subscribers.get("rate_limited", {})
    hosts, routed = (ipv4.hosts() if ipv4 else None), set()
    for p in sorted(plan, key=lambda p: (p["n"], p["type"])):
        index = p["n"] - 1
        p["vrf"] = vrfs[index % len(vrfs)]
        p["addressing"] = "radius" if ipv4 and (index // len(vrfs)) % 2 == 0 else "pool"
        p["framed_ip"] = p["delegated_prefix"] = p["framed_route"] = ""
        if p["addressing"] == "radius":
            try:
                p["framed_ip"] = str(next(hosts))
                p["delegated_prefix"] = str(next(pds)) if pds else ""
            except StopIteration as err:
                raise ValueError("lab_bng.radius ranges are too small for the RADIUS-addressed subscribers") from err
            if routes and p["type"] not in routed:
                p["framed_route"] = str(next(routes))
                routed.add(p["type"])
        p["rate_limit_mbps"] = int(limited["mbps"]) if limited and p["n"] == int(limited["n"]) else 0


def enterprise_plan(enterprise, links, areas, vrfs=None):
    """Validates the enterprise customers and works out their interfaces and addresses.

    Each customer has its own S-tag on an access link (TPID from the link), and
    optionally a C-tag for a tagged handoff. Returns one entry per customer:

        {name, link, vrf, tpid, protocol, s_tag, c_tag, outer_if, vsr_if,
         vsr_ipv4, cust_ipv4, vsr_ipv6, cust_ipv6, routed_ipv6, routed_ipv6_host}

    vrf is the customer's subscriber L3VRF: its own, or the first of vrfs.

    vsr_if is the VSR interface that holds the addresses: the S-tag interface
    outer_if (untagged handoff) or the C-tag interface on it (tagged handoff).
    Addresses are "address/prefix" for the VSR and plain addresses for the
    customer. Raises ValueError on an invalid customer.
    """
    access = {link["name"]: link for link in links if link["role"] == "access"}
    first = next(iter(access))
    area_tags = {int(area["s_tag"]) for area in areas}
    plan, names, s_tags = [], set(), set()
    for e in enterprise:
        name = e.get("name", "")
        where = f"lab_enterprise entry {name or '(no name)'}"
        if not name or name in names:
            raise ValueError(f"{where}: needs a unique name")
        link = e.get("link", first)
        if link not in access:
            raise ValueError(f"{where}: link {link} is not an access link")
        s_tag = int(e.get("s_tag", 0))
        if not 1 <= s_tag <= 4094 or s_tag in s_tags or s_tag in area_tags:
            raise ValueError(f"{where}: s_tag must be 1-4094 and not used by another customer or an access area")
        c_tag = e.get("c_tag")
        if c_tag is not None and not 1 <= int(c_tag) <= 4094:
            raise ValueError(f"{where}: c_tag must be 1-4094")
        try:
            v4 = ipaddress.IPv4Network(e["ipv4"])
            v6 = ipaddress.IPv6Network(e["ipv6"])
            routed = ipaddress.IPv6Network(e["routed_ipv6"])
        except (KeyError, ValueError) as err:
            raise ValueError(f"{where}: needs ipv4, ipv6 and routed_ipv6 networks ({err})") from err
        if v4.prefixlen != 31 or v6.prefixlen != 64:
            raise ValueError(f"{where}: ipv4 must be a /31 and ipv6 a /64")
        outer_if = f"{link}-{s_tag}"
        vsr_if = outer_if if c_tag is None else f"{outer_if}-{int(c_tag)}"
        if len(vsr_if) > 15:
            raise ValueError(f"{where}: VSR interface name {vsr_if} is longer than 15 characters; shorten the link name")
        vrf = e.get("vrf", vrfs[0] if vrfs else "")
        if vrfs and vrf not in vrfs:
            raise ValueError(f"{where}: vrf {vrf} is not a subscriber L3VRF ({', '.join(vrfs)})")
        tpid = access[link].get("tpid", "0x8100")
        names.add(name)
        s_tags.add(s_tag)
        plan.append({
            "name": name,
            "link": link,
            "vrf": vrf,
            "tpid": tpid,
            "protocol": "802.1ad" if tpid == "0x88a8" else "802.1q",
            "s_tag": s_tag,
            "c_tag": None if c_tag is None else int(c_tag),
            "outer_if": outer_if,
            "vsr_if": vsr_if,
            "vsr_ipv4": f"{v4[0]}/31",
            "cust_ipv4": str(v4[1]),
            "vsr_ipv6": f"{v6[1]}/64",
            "cust_ipv6": str(v6[2]),
            "routed_ipv6": str(routed),
            "routed_ipv6_host": str(routed[1]),
        })
    return plan


class FilterModule:
    def filters(self):
        return {"bnglab_subscribers": subscribers_plan, "bnglab_enterprise_plan": enterprise_plan}
