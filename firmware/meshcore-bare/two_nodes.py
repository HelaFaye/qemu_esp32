#!/usr/bin/env python3
"""Two meshcore-bare-esp32s3 nodes in QEMU on one air: each boots, brings its SX1262 up,
broadcasts its boot advert, hears the other's and stores it as a contact.

    two_nodes.py FLASH.bin WORKDIR
Needs qemu-system-xtensa with patches/espressif-qemu (QEMU_XTENSA, QEMU_BIOS).
"""
import os
import socket
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from boards import boot  # noqa: E402

CHECKS = [
    # (what, regex, timeout s)
    ("boots", r"app_main|main_task", 60),
    ("radio up", r"tcxo locked|xtal fallback", 60),
    ("advert sent", r"broadcast boot advert", 60),
    ("packet sent", r"tx_done at \d+ ms", 60),
    ("heard the other", r"radio rx wire=\d+ rssi=-?\d+", 120),
    ("contact stored", r"contact stored: name=", 60),
]
FATAL = r"Guru Meditation|abort\(\) was called|Backtrace:|rst:0x[0-9a-f]+ \((?!POWERON)"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main():
    flash, work = sys.argv[1], sys.argv[2]
    os.makedirs(work, exist_ok=True)
    port = free_port()
    hub = subprocess.Popen([sys.executable, os.path.join(HERE, "..", "..", "radio", "airhub.py"),
                            "--port", str(port), "--log"],
                           stderr=open(os.path.join(work, "airhub.log"), "w"))
    import time
    time.sleep(1)
    nodes = [boot("meshcore-bare", flash, work, name="node%d" % i, air=port) for i in range(2)]
    ok = True
    try:
        for what, rx, timeout in CHECKS:
            for n in nodes:
                m = n.wait_for(rx, timeout)
                print("%-6s %-16s %s" % (n.name, what, ("ok: " + m.group(0)) if m else "MISSING"))
                ok &= bool(m)
            if not ok:
                break
    finally:
        for n in nodes:
            n.stop()
        hub.kill()
    for n in nodes:
        log = n.log()
        bad = __import__("re").search(FATAL, log)
        if bad:
            ok = False
        print("\n===== %s serial (last 120 lines)%s =====" % (n.name, " FATAL" if bad else ""))
        print("\n".join(log.splitlines()[-120:]))
    print("\n===== air hub =====")
    print(open(os.path.join(work, "airhub.log")).read()[-3000:])
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
