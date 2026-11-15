#!/usr/bin/env python3
"""Wi-Fi on the emulated ESP32-S3, with the minimal app (src/main.c), twice:

- no network: it gets through the PHY's calibrations and the MAC's reset, its station
  starts, and a scan completes, finding nothing (patch 0015);
- an access point (patch 0019, boot(..., wifi=True)): the scan lists "Open Wifi", the
  station connects, DHCP gives it an address, and a UDP round trip reaches the host (an
  echo server here, on port 5555: 10.0.2.2 from the board) and comes back.

    firmware/wifi-start/build.sh OUT && firmware/wifi-start/check.py OUT/flash.bin WORKDIR
"""
import os
import re
import socket
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from boards import boot  # noqa: E402

STARTUP = [("Wi-Fi initialized", r"wifi: init ESP_OK"),
           ("PHY calibrated", r"phy_init: Saving new calibration data[^\n]*"),
           ("station started", r"wifi: station started")]
ALONE = STARTUP + [("scan finds nothing", r"wifi: scan done, 0 networks")]
WITH_AP = STARTUP + [("scan finds the AP", r'wifi: network "Open Wifi" channel 4 rssi -\d+'),
                     ("connects", r"wifi: connected to Open Wifi"),
                     ("DHCP address", r"wifi: got ip 10\.0\.2\.\d+ gateway 10\.0\.2\.2"),
                     ("UDP round trip", r'udp: sent "hello \d+", got "echo: hello \d+"')]


def echo_server(port=5555):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", port))

    def serve():
        while True:
            data, peer = s.recvfrom(2048)
            s.sendto(b"echo: " + data, peer)
    threading.Thread(target=serve, daemon=True).start()


def run(title, flash, work, checks, **kw):
    print(title)
    n = boot("esp32s3-devkitc", flash, work, name="wifi", **kw)
    ok = True
    try:
        for what, rx in checks:
            m = n.wait_for(rx, 60)
            print("  %-20s %s" % (what, ("ok: " + m.group(0)) if m else "MISSING"))
            ok &= bool(m)
    finally:
        n.stop()
    bad = re.search(r"Guru Meditation|abort\(\)|Backtrace:", n.log())
    print("  %-20s %s" % ("no panic", "ok" if not bad else "FAIL: " + bad.group(0)))
    if not ok or bad:
        print("\n".join(n.log().splitlines()[-30:]))
    return ok and not bad


def main():
    flash, work = sys.argv[1:3]
    echo_server()
    results = []
    for title, sub, checks, kw in (("No network", "alone", ALONE, {}),
                                   ("An access point", "ap", WITH_AP, {"wifi": True})):
        d = os.path.join(work, sub)
        os.makedirs(d, exist_ok=True)
        results.append(run(title, flash, d, checks, **kw))
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
