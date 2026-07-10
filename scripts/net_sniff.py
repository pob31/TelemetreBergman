#!/usr/bin/env python3
"""Raw-socket ARP / TCP-handshake sniffer — "why can't this device load the page?"

For the case where one phone/tablet suddenly cannot reach the readout while other
devices can, and that same device still loads the router's own admin page.
It answers one question: **do the device's packets even arrive at the Pi?**

`tcpdump` is often not installed, and the Pi may have no internet to `apt` it, so
this uses the standard library only (like frames.py / filters.py).

On the chosen interface it logs:
  * every ARP `who-has` / `is-at`
  * every TCP SYN / RST / FIN on the web port (handshake events only, no payload)

Run it as root (raw sockets need it):

    sudo python3 scripts/net_sniff.py                  # eth0, port 80
    sudo python3 scripts/net_sniff.py wlan0 8080       # pick interface + port

Leave it running in the background while you reproduce the fault:

    sudo systemd-run --unit=tb-sniff python3 ~/TelemetreBergman/scripts/net_sniff.py
    journalctl -u tb-sniff -f            # watch, then reproduce
    sudo systemctl stop tb-sniff         # transient unit; also clears on reboot

Reading it while the device is blocked — see the table in the README section
"Troubleshooting — one device can't load the page". In short: if nothing at all
shows up from that device, its frames never reached the Pi, and the fault is the
switch/AP/router, not this machine.
"""
from __future__ import annotations

import socket
import struct
import sys
import time

ETH_P_ALL = 0x0003
ETH_ARP = 0x0806
ETH_IPV4 = 0x0800

TCP_FLAGS = ((0x02, "SYN"), (0x10, "ACK"), (0x01, "FIN"), (0x04, "RST"))
SYN_FIN_RST = 0x07  # only these are interesting; everything else is payload/ACK


def _mac(b: bytes) -> str:
    return ":".join(f"{x:02x}" for x in b)


def _ip(b: bytes) -> str:
    return ".".join(str(x) for x in b)


def sniff(iface: str = "eth0", port: int = 80) -> None:
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(ETH_P_ALL))
    sock.bind((iface, 0))
    print(f"# sniffing {iface} (ARP + TCP :{port} handshakes) "
          f"{time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)

    while True:
        pkt = sock.recvfrom(65535)[0]
        if len(pkt) < 14:
            continue
        ts = time.strftime("%H:%M:%S")
        etype = struct.unpack("!H", pkt[12:14])[0]

        if etype == ETH_ARP and len(pkt) >= 42:
            arp = pkt[14:42]
            op = struct.unpack("!H", arp[6:8])[0]
            kind = "who-has" if op == 1 else "is-at  "
            print(f"{ts} ARP {kind} src={_mac(arp[8:14])}/{_ip(arp[14:18])} "
                  f"target={_ip(arp[24:28])}", flush=True)

        elif etype == ETH_IPV4 and len(pkt) >= 34:
            ihl = (pkt[14] & 0x0F) * 4
            proto = pkt[23]
            src, dst = _ip(pkt[26:30]), _ip(pkt[30:34])
            off = 14 + ihl

            if proto == 6 and len(pkt) >= off + 14:            # TCP
                sport, dport = struct.unpack("!HH", pkt[off:off + 4])
                if port not in (sport, dport):
                    continue
                flags = pkt[off + 13]
                if not flags & SYN_FIN_RST:
                    continue
                names = "".join(n for bit, n in TCP_FLAGS if flags & bit)
                print(f"{ts} TCP {src}:{sport} -> {dst}:{dport} [{names}]", flush=True)

            elif proto == 1:                                    # ICMP
                print(f"{ts} ICMP {src} -> {dst}", flush=True)


def main() -> None:
    iface = sys.argv[1] if len(sys.argv) > 1 else "eth0"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 80
    try:
        sniff(iface, port)
    except PermissionError:
        sys.exit(f"raw sockets need root:  sudo python3 {' '.join(sys.argv)}")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
