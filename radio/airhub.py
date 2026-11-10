#!/usr/bin/env python3
"""The air between emulated LoRa radios (QEMU's sx1262 device, chardev=).

Each QEMU connects to the hub with
    -chardev socket,id=air,host=127.0.0.1,port=7400 -device sx1262,...,chardev=air
A packet a radio starts is sent on to every other radio as that radio hears it: an RSSI and
SNR from the link between them, lost when the link is out of range or by chance, and
corrupted (a CRC error) by chance. Collisions happen in the radios, which see the packets'
times and strengths.

Links (--links FILE, JSON) are by node name; a node is named by its connection order
("0", "1", ...) unless the QEMU sends a hello first (not needed):
    {"default": {"rssi": -70, "snr": 8, "loss": 0.0, "corrupt": 0.0},
     "links": {"0-1": {"rssi": -95, "snr": 2, "loss": 0.1}, "0-2": null}}
null makes a pair out of range. Links are symmetric unless "1-0" is given too.

    python3 airhub.py --port 7400 [--links links.json] [--seed 1] [--log]
"""
import argparse
import asyncio
import json
import random
import struct
import sys

MAGIC = 0x41524F4C
AIR_TX, AIR_RX = 1, 2
# magic, len, type, sf, freq, bw, cr, flags, sync, power, rssi, snr_q, corrupt, airtime, preamble, payload_len
HEADER = struct.Struct("<IHBBIIBBHbbbBIHH")


class Link:
    def __init__(self, rssi=-70, snr=8.0, loss=0.0, corrupt=0.0):
        self.rssi, self.snr, self.loss, self.corrupt = rssi, snr, loss, corrupt


class Hub:
    def __init__(self, links=None, seed=None, log=False):
        self.nodes = []             # (name, writer)
        self.default = Link()
        self.links = {}
        self.rng = random.Random(seed)
        self.log = log
        self.count = 0
        if links:
            if "default" in links:
                self.default = Link(**links["default"])
            for k, v in links.get("links", {}).items():
                self.links[k] = None if v is None else Link(**{**vars(self.default), **v})

    def link(self, a, b):
        for key in ("%s-%s" % (a, b), "%s-%s" % (b, a)):
            if key in self.links:
                return self.links[key]
        return self.default

    def deliver(self, src, frame):
        """frame: a whole AIR_TX frame from src. Returns [(name, bytes)] to send."""
        h = list(HEADER.unpack_from(frame))
        payload = frame[HEADER.size:]
        out = []
        for name, _ in self.nodes:
            if name == src:
                continue
            link = self.link(src, name)
            if link is None or self.rng.random() < link.loss:
                continue
            h[2] = AIR_RX
            h[10] = max(-128, min(127, int(round(link.rssi))))
            h[11] = max(-128, min(127, int(round(link.snr * 4))))
            h[12] = 1 if self.rng.random() < link.corrupt else 0
            out.append((name, HEADER.pack(*h) + payload))
        return out

    async def serve(self, reader, writer):
        name = str(self.count)
        self.count += 1
        self.nodes.append((name, writer))
        if self.log:
            print("node %s connected" % name, file=sys.stderr)
        buf = b""
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                buf += data
                while len(buf) >= 6:
                    magic, n = struct.unpack_from("<IH", buf)
                    if magic != MAGIC:
                        buf = buf[1:]
                        continue
                    if len(buf) < 6 + n:
                        break
                    frame, buf = buf[:6 + n], buf[6 + n:]
                    if frame[6] != AIR_TX:
                        continue
                    if self.log:
                        h = HEADER.unpack_from(frame)
                        print("node %s: %d bytes at %.3f MHz SF%d, %d us" %
                              (name, h[15], h[4] / 1e6, h[3], h[13]), file=sys.stderr)
                    for dst, out in self.deliver(name, frame):
                        for n2, w in self.nodes:
                            if n2 == dst:
                                w.write(out)
        finally:
            self.nodes = [(n, w) for n, w in self.nodes if w is not writer]
            writer.close()


async def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7400)
    ap.add_argument("--links")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--log", action="store_true")
    a = ap.parse_args()
    links = json.load(open(a.links)) if a.links else None
    hub = Hub(links, a.seed, a.log)
    server = await asyncio.start_server(hub.serve, a.host, a.port)
    print("LoRa air hub on %s:%d" % (a.host, a.port), file=sys.stderr)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
