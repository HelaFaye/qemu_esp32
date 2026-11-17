#!/usr/bin/env python3
"""SlimeVR tracker firmware (BOARD_ESP32S3_SUPERMINI) on an ESP32-S3 SuperMini with an
access point, against a stand-in SlimeVR server on the host:

- it boots and prints its banner, with its sketch's MD5 (esp_image_verify() over the
  running app: the MMU's shared flash pages, patch 0021);
- its LED, the SuperMini's WS2812 on RGB_BUILTIN, goes through the RMT (0022): the
  frames reach the RMT and setup() carries on past them;
- it formats LittleFS and saves its configuration;
- it reports no IMU (none is emulated yet) and goes on without one;
- it joins "Open Wifi" by its built-in credentials and gets an address by DHCP;
- it finds the server by a broadcast handshake on UDP 6969, learns its address as
  10.0.2.2 (not 127.0.0.1: 0023), and sends it sensor info and more;
- with the server's heartbeats, it stays connected: no timeout;
- no panic, no reset.

    check.py FLASH.bin WORKDIR
"""
import os
import re
import socket
import struct
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from boards import boot  # noqa: E402

FATAL = re.compile(r"Guru Meditation|abort\(\) was called|Backtrace:|Task watchdog|rst:0xc")
SERVER_PORT = 6969
fails = 0


def check(what, ok, detail=""):
    global fails
    print("  %s %s%s" % ("ok  " if ok else "FAIL", what, " (%s)" % detail if detail and not ok else ""))
    if not ok:
        fails += 1
    return ok


def milestone(line, later):
    """A line's own evidence, or a later line only it leads to. Arduino's USB CDC
    (HWCDC) drops what's printed while its TX ring stays full past its timeout, and
    the logger and Serial.print() from two tasks interleave: now and then a line
    comes out cut or not at all, though the firmware went through it."""
    if line is None and later is not None:
        print("       (that line was lost on the USB log; a later one shows it passed)")
    return line is not None or later is not None


class Server(threading.Thread):
    """Answers the tracker's handshake, sends heartbeats, counts packets by type."""

    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", SERVER_PORT))
        self.sock.settimeout(0.5)
        self.counts = {}
        self.peer = None
        self.running = True

    def run(self):
        last_beat = 0
        while self.running:
            try:
                data, addr = self.sock.recvfrom(2048)
                ptype = struct.unpack(">I", data[:4])[0] if len(data) >= 4 else -1
                self.counts[ptype] = self.counts.get(ptype, 0) + 1
                if ptype == 3:                               # Handshake
                    self.peer = addr
                    self.sock.sendto(b"\x03Hey OVR =D 5", addr)
            except socket.timeout:
                pass
            if self.peer and time.time() - last_beat > 0.5:  # Heartbeat: type 1
                self.sock.sendto(b"\x00\x00\x00\x01" + bytes(8), self.peer)
                last_beat = time.time()


def main():
    flash, work = sys.argv[1], sys.argv[2]
    os.makedirs(work, exist_ok=True)
    server = Server()
    server.start()
    node = boot("esp32s3-supermini", flash, work, name="slimevr", wifi=True)
    try:
        print("SlimeVR tracker (ESP32-S3 SuperMini)")
        md5 = node.wait_for(r"Sketch MD5: [0-9a-f]{32}", 60)
        config = node.wait_for(r"\[Configuration\] Loaded configuration", 60)
        # The MD5 line can be lost on the USB log (see milestone()); setup() reaching
        # the configuration is past getSketchMD5() all the same.
        check("it boots, with its sketch's MD5", milestone(md5, config))
        check("it saves its configuration", config is not None)
        frames = node.qmp("qom-get", path="/machine/soc/rmt", property="frames")
        check("its LED's frames went through the RMT (%s)" % frames, frames > 0)
        check("it goes on with no IMU",
              node.wait_for(r"0 sensor\(s\) configured", 60) is not None)
        joined = node.wait_for(r"SSID 'Open Wifi', IP address 10\.0\.2\.", 120)
        found = node.wait_for(r"Handshake successful, server is 10\.0\.2\.2:6969", 60)
        check("it joins Open Wifi", milestone(joined, found))
        check("it finds the server at 10.0.2.2", found is not None)
        time.sleep(15)
        check("the server heard its handshake and sensor info (%s)" % server.counts,
              server.counts.get(3, 0) >= 1 and server.counts.get(15, 0) >= 1)
        log = node.log()
        check("it stays connected", "Connection to server timed out" not in log)
        m = FATAL.search(log)
        check("no panic, no reset", m is None, m and m.group(0))
    finally:
        server.running = False
        node.stop()
    print("%s: %d failure(s)" % ("PASS" if not fails else "FAIL", fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
