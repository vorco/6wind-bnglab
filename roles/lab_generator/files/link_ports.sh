#!/bin/sh
# Lists the generator's passthrough Ethernet ports (everything but the virtio
# management NIC), one PCI address per line, in PCI order. Proxmox adds them in
# lab_links order, so this order matches the inventory.
for d in /sys/bus/pci/devices/*; do
  [ "$(cut -c1-6 "$d/class")" = "0x0200" ] || continue
  [ "$(cat "$d/vendor")" = "0x1af4" ] && continue
  basename "$d"
done | sort
