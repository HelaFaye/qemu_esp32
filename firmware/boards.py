"""Boards as QEMU command lines: the chip, its flash, and what the board wires to its pins.

A board is a function returning the -device/-chardev arguments after the machine and flash;
boot() starts QEMU with one and returns a Node whose serial log can be waited on. The
devices come from patches/espressif-qemu (pin-link, sx1262, displays, ...).
"""
import os
import re
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
QEMU = os.environ.get("QEMU_XTENSA", "qemu-system-xtensa")
QEMU_BIOS = os.environ.get("QEMU_BIOS")        # Directory with esp32s3-rom.bin etc.


def link(pin, dev, line, *, out=False, open_drain=False, pull=None):
    a = "esp32s3.pin-link,pin=%d,dev=%s,line=%s" % (pin, dev, line)
    if out:
        a += ",dir=out"
    if open_drain:
        a += ",open-drain=on"
    if pull:
        a += ",pull=" + pull
    return ["-device", a]


def sx1262(nss, reset, busy, dio1, *, bus="spi2", cs=1, air=None, name="lora"):
    args = []
    dev = "sx1262,bus=%s,cs=%d,id=%s" % (bus, cs, name)
    if air:
        args += ["-chardev", "socket,id=%s-air,host=127.0.0.1,port=%d" % (name, air)]
        dev += ",chardev=%s-air" % name
    args += ["-device", dev]
    args += link(nss, name, "ssi-gpio-cs", pull="up")
    args += link(reset, name, "reset", pull="up")
    args += link(busy, name, "busy", out=True)
    args += link(dio1, name, "dio1", out=True)
    return args


# ---------------------------------------------------------------------------------------
# Boards

def meshcore_bare(air=None, **_):
    """mytechnotalent/meshcore-bare-esp32s3: an ESP32-S3 DevKitC and an SX1262 module
    (NSS 7, SCK 5, MISO 3, MOSI 6, DIO1 16, BUSY 15, RESET 8, RXEN 4, TXEN 9)."""
    return "esp32s3", sx1262(nss=7, reset=8, busy=15, dio1=16, air=air)


def gbagate_carrier_touch(card=None, **_):
    """HelaFaye/gbagate's touch carrier (its doc/CARRIER.md): an ESP32-S3 Supermini, an
    ILI9341 320x240 with an XPT2046 on GP-SPI2 (SCK 12, MOSI 11, MISO 13; LCD CS 10,
    D/C 9; touch CS 16, PENIRQ 17), a micro SD (CS 15; card: a raw image), and the
    Battle Chip board's two MCP23017s on I2C0 (SDA 1, SCL 2). Pins: its
    port/esp32s3/board.h at firmware/gbagate/COMMIT. Its own check, which also presses
    the touch and reads the glass, is firmware/gbagate/check.sh."""
    args = ["-device", "ili9341,bus=spi2,id=lcd"]
    args += link(10, "lcd", "ssi-gpio-cs", pull="up") + link(9, "lcd", "dc")
    args += ["-device", "xpt2046,bus=spi2,cs=1,id=touch"]
    args += link(16, "touch", "ssi-gpio-cs", pull="up")
    args += link(17, "touch", "penirq", out=True, open_drain=True, pull="up")
    if card:
        args += ["-drive", "id=card,if=none,format=raw,file=%s" % card,
                 "-device", "ssi-sd,bus=spi2,cs=2,id=sd,drive=card"]
        args += link(15, "sd", "ssi-gpio-cs", pull="up")
    args += ["-device", "mcp23017,bus=i2c0,address=0x20,id=exp0",
             "-device", "mcp23017,bus=i2c0,address=0x21,id=exp1"]
    return "esp32s3", args


def gbagate_devkit(**_):
    """HelaFaye/gbagate on an ESP32-S3 DevKitC with a 128x128 ST7735 (a BGR module) on
    GP-SPI2: CS 14, D/C 21."""
    args = ["-device", "st7735,bus=spi2,id=lcd,width=128,height=128,x-offset=2,y-offset=3,"
                       "panel-bgr=on"]
    args += link(14, "lcd", "ssi-gpio-cs", pull="up") + link(21, "lcd", "dc")
    return "esp32s3", args


BOARDS = {
    "meshcore-bare": meshcore_bare,
    "gbagate-carrier-touch": gbagate_carrier_touch,
    "gbagate-devkit": gbagate_devkit,
}


class Node:
    def __init__(self, name, proc, log_path):
        self.name, self.proc, self.log_path = name, proc, log_path

    def log(self):
        """UART0 and the USB serial (the console is on one or the other)."""
        out = ""
        for p in (self.log_path, self.log_path[:-4] + "-usb.log"):
            try:
                with open(p, errors="replace") as f:
                    out += f.read()
            except OSError:
                pass
        return out

    def wait_for(self, pattern, timeout=60):
        """The first match of a regex in the serial log, or None after timeout."""
        rx = re.compile(pattern)
        end = time.time() + timeout
        while time.time() < end:
            m = rx.search(self.log())
            if m:
                return m
            if self.proc.poll() is not None:
                break
            time.sleep(0.2)
        return None

    def count(self, pattern):
        return len(re.findall(pattern, self.log()))

    def qemu_output(self):
        """What QEMU itself printed (errors when it wouldn't start)."""
        try:
            with open(self.log_path[:-4] + "-qemu.log", errors="replace") as f:
                return f.read()
        except OSError:
            return ""

    def stop(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()


def boot(board, flash, workdir, name="node", hci=None, **opts):
    """Boot flash (a merged image) on a board. UART0 goes to <workdir>/<name>.log, the
    USB serial (plugged in) to <name>-usb.log; hci: a TCP port for UART1 (a virtual
    Bluetooth controller), else nothing on it."""
    machine, devices = BOARDS[board](**opts)
    log = os.path.join(workdir, name + ".log")
    img = os.path.join(workdir, name + "-flash.bin")
    with open(flash, "rb") as src, open(img, "wb") as dst:
        data = src.read()                      # Each node its own flash (NVS: its identity)
        size = 4 << 20
        while size < len(data):
            size *= 2                          # QEMU wants the whole chip: 4, 8 or 16 MB
        dst.write(data + b"\xff" * (size - len(data)))
    args = [QEMU, "-machine", machine, "-nographic", "-monitor", "none", "-nic", "none",
            "-drive", "file=%s,if=mtd,format=raw" % img,
            "-serial", "file:" + log,
            "-serial", "tcp:127.0.0.1:%d" % hci if hci else "null",
            "-serial", "null",
            "-serial", "file:" + log[:-4] + "-usb.log"]
    if QEMU_BIOS:
        args += ["-L", QEMU_BIOS]
    args += devices
    with open(os.path.join(workdir, name + "-qemu.txt"), "w") as f:
        f.write(" ".join(args) + "\n")
    proc = subprocess.Popen(args, stdout=open(os.path.join(workdir, name + "-qemu.log"), "w"),
                            stderr=subprocess.STDOUT)
    return Node(name, proc, log)
