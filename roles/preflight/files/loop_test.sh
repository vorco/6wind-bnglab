#!/bin/bash
# Proves two host network interfaces are cabled to each other.
# Usage: loop_test.sh <ifname-a> <ifname-b> [carrier-timeout-seconds]
# Each interface is moved into its own temporary network namespace, given a
# link-local address, and pinged across. Both interfaces are always returned
# to the host namespace in their original up/down state, even on failure.
set -u
a=$1 b=$2 timeout=${3:-20}
ns_a=bnglab-loop-a ns_b=bnglab-loop-b
was_up_a=$(ip -o link show "$a" | grep -c '[<,]UP[,>]')
was_up_b=$(ip -o link show "$b" | grep -c '[<,]UP[,>]')

restore() {
  for pair in "$ns_a:$a:$was_up_a" "$ns_b:$b:$was_up_b"; do
    IFS=: read -r ns dev up <<<"$pair"
    if ip netns list | grep -qw "$ns"; then
      ip -n "$ns" addr flush dev "$dev" 2>/dev/null
      ip -n "$ns" link set dev "$dev" down 2>/dev/null
      ip -n "$ns" link set dev "$dev" netns 1 2>/dev/null
      ip netns delete "$ns"
    fi
    [ "$up" = 1 ] && ip link set dev "$dev" up
  done
}
trap restore EXIT

ip netns add "$ns_a" && ip netns add "$ns_b" || { echo "result=error reason=netns"; exit 1; }
ip link set dev "$a" netns "$ns_a" && ip link set dev "$b" netns "$ns_b" || { echo "result=error reason=move"; exit 1; }
ip -n "$ns_a" link set dev "$a" up
ip -n "$ns_b" link set dev "$b" up

carrier() { ip netns exec "$1" cat "/sys/class/net/$2/carrier" 2>/dev/null; }
for _ in $(seq "$timeout"); do
  [ "$(carrier "$ns_a" "$a")" = 1 ] && [ "$(carrier "$ns_b" "$b")" = 1 ] && break
  sleep 1
done
echo "carrier_a=$(carrier "$ns_a" "$a") carrier_b=$(carrier "$ns_b" "$b")"
if [ "$(carrier "$ns_a" "$a")" != 1 ] || [ "$(carrier "$ns_b" "$b")" != 1 ]; then
  echo "result=fail reason=no-carrier"; exit 1
fi

ip -n "$ns_a" addr add 169.254.99.1/30 dev "$a"
ip -n "$ns_b" addr add 169.254.99.2/30 dev "$b"
if ip netns exec "$ns_a" ping -q -c 3 -i 0.3 -W 2 169.254.99.2 >/dev/null; then
  echo "result=pass"
else
  echo "result=fail reason=no-ping"; exit 1
fi
