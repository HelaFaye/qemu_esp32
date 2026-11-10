#!/usr/bin/env python3
"""Two meshcore-bare-esp32s3 nodes in QEMU on one LoRa air, each with its phone.

Each node's NimBLE host (UART1, HCI) talks to a virtual controller on vbt's radio; a
virtual companion app finds the node, pairs (passkey 123456) and sets its clock, as the
MeshCore app does on connecting. Then each node broadcasts its advert over LoRa, hears the
other's and stores it as a contact.

    two_nodes.py FLASH.bin WORKDIR
Needs qemu-system-xtensa with patches/espressif-qemu (QEMU_XTENSA, QEMU_BIOS) and bumble.
"""
import asyncio
import os
import re
import socket
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "bluetooth"))
from boards import boot  # noqa: E402

FATAL = r"Guru Meditation|abort\(\) was called|Backtrace:|rst:0x[0-9a-f]+ \((?!POWERON)"
ADDRESSES = ["24:0A:C4:00:00:01", "24:0A:C4:00:00:02"]


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Radio:
    """vbt's world in its own asyncio thread."""

    def __init__(self):
        from vbt import World
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.world = self.run(self._make(World))

    @staticmethod
    async def _make(World):
        return World()

    def run(self, coro, timeout=60):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)


def check(results, node, what, m):
    ok = bool(m)
    print("%-6s %-22s %s" % (node, what, ("ok: " + (m.group(0) if hasattr(m, "group") else str(m))) if ok
                             else "MISSING"))
    results.append(ok)
    return ok


def main():
    flash, work = sys.argv[1], sys.argv[2]
    os.makedirs(work, exist_ok=True)
    air = free_port()
    hub = subprocess.Popen([sys.executable, os.path.join(HERE, "..", "..", "radio", "airhub.py"),
                            "--port", str(air), "--log"],
                           stderr=open(os.path.join(work, "airhub.log"), "w"))
    time.sleep(1)

    from phone import Phone
    radio = Radio()
    controllers, nodes, results = [], [], []
    for i in range(2):
        port = free_port()
        # The controller listens; the node's UART1 connects to it when QEMU starts
        controllers.append(radio.run(radio.world.attach_hci("tcp-server:127.0.0.1:%d" % port,
                                                            address=ADDRESSES[i], name="node%d" % i)))
        nodes.append(boot("meshcore-bare", flash, work, name="node%d" % i, air=air, hci=port))

    try:
        for n in nodes:
            if not check(results, n.name, "boots", n.wait_for(r"main_task: Calling app_main", 60)):
                raise SystemExit
        for n in nodes:
            check(results, n.name, "radio up", n.wait_for(r"sx1262: init=ok[^\n]*", 60))
        for n in nodes:              # Informational: the firmware's own wording
            m = n.wait_for(r"NimBLE[^\n]*(sync|advertis)[^\n]*|ble_companion[^\n]*(advert|ready|start)[^\n]*", 20)
            print("%-6s %-22s %s" % (n.name, "BLE host (log)", m.group(0) if m else "(nothing logged)"))

        # Each phone pairs with one node and sets its clock
        async def phones():
            done = []
            for i in range(2):
                p = Phone(radio.world, "AA:BB:CC:00:00:%02X" % (i + 1), name="Phone%d" % i)
                await p.start()
                addr = await p.find(timeout=60, exclude=[a for a, _, _ in done])
                await p.connect(addr)
                ok = await p.set_time()
                done.append((str(addr), p.connection.is_encrypted, ok))
            return done
        try:
            for (addr, enc, ok), n in zip(radio.run(phones(), timeout=240), nodes):
                check(results, n.name, "phone paired, set time", ok and enc and addr)
        except Exception as e:
            print("phones: %r" % e)
            results.append(False)

        for what, rx, timeout in [("advert sent", r"broadcast boot advert wire=\d+", 60),
                                  ("packet sent", r"tx_done at \d+ ms", 60),
                                  ("heard the other", r"radio rx wire=\d+ rssi=-?\d+ snr=-?\d+", 120),
                                  ("contact stored", r"contact stored: name=[^\n]*", 60)]:
            for n in nodes:
                check(results, n.name, what, n.wait_for(rx, timeout))
    finally:
        for n in nodes:
            n.stop()
        hub.kill()
        for i, c in enumerate(controllers):
            try:
                c.write_btsnoop(os.path.join(work, "node%d-hci.btsnoop" % i))
                open(os.path.join(work, "node%d-hci.txt" % i), "w").write(c.trace_text(400))
            except Exception:
                pass

    ok = all(results) and len(results) > 0
    for i, n in enumerate(nodes):
        log = n.log()
        bad = re.search(FATAL, log)
        ok &= not bad
        if n.proc.returncode not in (None, 0, -9):
            print("\n===== %s: QEMU exited %s =====\n%s" % (n.name, n.proc.returncode, n.qemu_output()[-3000:]))
        print("\n===== %s serial (last 100 lines)%s =====" % (n.name, " FATAL" if bad else ""))
        print("\n".join(log.splitlines()[-100:]))
        try:
            print("\n===== %s HCI (last 60) =====\n%s" % (n.name, "\n".join(
                open(os.path.join(work, "node%d-hci.txt" % i)).read().splitlines()[-60:])))
        except OSError:
            pass
    print("\n===== air hub =====")
    print(open(os.path.join(work, "airhub.log")).read()[-3000:])
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
