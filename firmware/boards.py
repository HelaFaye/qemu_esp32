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


def esp32s3_devkitc(**_):
    """An ESP32-S3 DevKitC on its own: the chip, its flash, the console on UART0 and USB."""
    return "esp32s3", []


def power_domain(name, *devices, pin=None):
    """Parts on their own supply (patch 0011), after them on the command line: switched
    by the firmware through pin (a regulator's enable) if given, and by a test with
    Node.part_power(name, on)."""
    args = ["-device", "power-domain,id=%s,devices=%s%s" % (name, ":".join(devices),
                                                          ",switched=on" if pin is not None else "")]
    if pin is not None:
        args += link(pin, name, "enable")
    return args


def bus_noise(name, *devices, ber=0.0, nack=0.0, burst=1, seed=1):
    """Errors on parts' wires (patch 0012), after them on the command line: bit flips on
    SPI and I2C at ber (per bit, each way), lost I2C ACKs at nack (per addressing),
    burst bits per error. Change at run time with Node.noise(name, ber=, nack=)."""
    return ["-device", "bus-noise,id=%s,devices=%s,ber=%g,nack=%g,burst=%d,seed=%d" %
            (name, ":".join(devices), ber, nack, burst, seed)]


def pin_glitch(name, pin, rate=0.0, width_us=1, seed=1):
    """Short pulses pulling an input low (patch 0012): noise, bounce. rate: a second, at
    random; Node.glitch(name, rate=) to change it, Node.glitch(name, pulse=True) for one."""
    return ["-device", "pin-glitch,id=%s,rate=%g,width-us=%d,seed=%d" % (name, rate, width_us, seed)] + \
        link(pin, name, "out", out=True, open_drain=True, pull="up")


def button(name, pin, key=None):
    return ["-device", "gpio-button,pin=%d,id=%s%s" % (pin, name, ",key=" + key if key else "")]


def t_deck(air=None, sd=None, **_):
    """LilyGO T-Deck (ESP32-S3, 16 MB flash, 8 MB PSRAM): a 320x240 ST7789 on SPI (CS 12,
    DC 11, backlight 42), an SX1262 (CS 9, RST 17, BUSY 13, DIO1 45) and a microSD (CS 39) on
    the same SPI2 pins (SCK 40, MOSI 41, MISO 38); I2C (SDA 18, SCL 8): the keyboard's
    ESP32-C3 (0x55) and a GT911 (0x5D, INT 16); the trackball (up 3, down 15, left 1,
    right 2, click 0); BOOT is GPIO0 too. examples/UnitTest/utilities.h."""
    args = ["-m", "8M", "-device", "st7789,bus=spi2,cs=0,id=lcd"]
    args += link(12, "lcd", "ssi-gpio-cs", pull="up") + link(11, "lcd", "dc") + link(42, "lcd", "backlight")
    args += sx1262(nss=9, reset=17, busy=13, dio1=45, cs=1, air=air)
    if sd:
        args += ["-drive", "id=sdcard,if=none,format=raw,file=" + sd,
                 "-device", "ssi-sd,bus=spi2,cs=2,id=sd,drive=sdcard"]
        args += link(39, "sd", "ssi-gpio-cs", pull="up")
    else:
        args += ["-device", "esp32s3.pin-link,pin=39,pull=up"]          # The card slot, empty
    args += ["-device", "tdeck-keyboard,bus=i2c0,address=0x55,id=kbd",
             "-device", "gt911,bus=i2c0,address=0x5d,id=touch,width=320,height=240"]
    args += link(16, "touch", "int", out=True)
    for name, pin, key in [("ball_up", 3, "up"), ("ball_down", 15, "down"), ("ball_left", 1, "left"),
                           ("ball_right", 2, "right"), ("ball_click", 0, "ret")]:
        args += button(name, pin, key)
    # BOARD_POWERON (GPIO10) switches the peripherals' supply; the firmware raises it first
    args += power_domain("periph_pwr", "lcd", "lora", "kbd", "touch", *(["sd"] if sd else []), pin=10)
    return "esp32s3", args


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


def from_kicad(path, sd=None, **_):
    """A board read from a KiCad design (.kicad_pcb or netlist): kicad/kiboard.py. sd: a
    raw image for the design's SD socket, if it has one."""
    import sys
    sys.path.insert(0, os.path.join(HERE, "..", "kicad"))
    import kiboard
    b = kiboard.board_from(path)
    if not b.machine:
        raise ValueError("%s: nothing to emulate:\n%s" % (path, b.report()))
    args = list(b.args)
    if sd:
        for i, a in enumerate(args):
            if a.startswith("ssi-sd,"):
                args[i] = a + ",drive=kicad_sd"
                args += ["-drive", "id=kicad_sd,if=none,format=raw,file=" + sd]
                break
    return b.machine, args


BOARDS = {
    "esp32s3-devkitc": esp32s3_devkitc,
    "meshcore-bare": meshcore_bare,
    "t-deck": t_deck,
    "gbagate-carrier-touch": gbagate_carrier_touch,
    "gbagate-devkit": gbagate_devkit,
}


class Node:
    def __init__(self, name, proc, log_path, qmp_path=None):
        self.name, self.proc, self.log_path = name, proc, log_path
        self.qmp_path = qmp_path or log_path[:-4] + "-qmp.sock"

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

    def qmp(self, command, **arguments):
        """One QMP command on the node's monitor socket; its "return"."""
        import json
        import socket
        sock = socket.socket(socket.AF_UNIX)
        sock.connect(self.qmp_path)
        f = sock.makefile("rw")
        f.readline()                                    # Greeting
        for msg in ({"execute": "qmp_capabilities"}, {"execute": command, "arguments": arguments}):
            f.write(json.dumps(msg) + "\n")
            f.flush()
            while True:
                r = json.loads(f.readline())
                if "return" in r or "error" in r:
                    break
        sock.close()
        if "error" in r:
            raise RuntimeError(r["error"])
        return r["return"]

    def screendump(self, path, device="lcd"):
        """A display's glass as a PPM file; (width, height, rgb bytes)."""
        self.qmp("screendump", filename=path, device=device)
        data = open(path, "rb").read()
        parts = data.split(b"\n", 3)
        w, h = map(int, parts[1].split())
        return w, h, parts[3]

    # -- The supply (patch 0010: RTC_CNTL's vdd-mv; the S3) ---------------------------

    RTC_CNTL = "/machine/soc/rtc_cntl"

    def set_vdd(self, mv):
        """The chip's supply, in millivolts (3300 normally). Below the brown-out
        detector's threshold (2440, ESP-IDF's default level) the detector trips: ESP-IDF
        reports it and restarts, again while it stays low; below 1800 the chip is off,
        and coming back is a power-on reset."""
        self.qmp("qom-set", path=self.RTC_CNTL, property="vdd-mv", value=int(mv))

    def vdd(self):
        return self.qmp("qom-get", path=self.RTC_CNTL, property="vdd-mv")

    def brownout(self, mv=2300, seconds=0.05, nominal=3300):
        """A dip to mv for a while (wall-clock time), then back to nominal."""
        self.set_vdd(mv)
        time.sleep(seconds)
        self.set_vdd(nominal)

    def power_cut(self, seconds=0.1, nominal=3300):
        """The supply gone (a pulled cable, a dead battery), then back: a power-on reset.
        Flash keeps what was written before the cut."""
        self.brownout(0, seconds, nominal)

    def part_power(self, domain, on=True):
        """Switch a power domain (boards.power_domain): off, its parts leave their buses;
        on again, they start from their power-on state."""
        self.qmp("qom-set", path="/machine/peripheral/" + domain, property="on", value=bool(on))

    def noise(self, name, ber=None, nack=None):
        """Set a bus_noise's rates; returns its counters (errors injected so far)."""
        path = "/machine/peripheral/" + name
        if ber is not None:
            self.qmp("qom-set", path=path, property="ber", value="%g" % ber)
        if nack is not None:
            self.qmp("qom-set", path=path, property="nack", value="%g" % nack)
        return {k: self.qmp("qom-get", path=path, property=k) for k in ("flips", "nacks")}

    def glitch(self, name, rate=None, pulse=False):
        """Set a pin_glitch's rate, or fire one pulse now; returns how many so far."""
        path = "/machine/peripheral/" + name
        if rate is not None:
            self.qmp("qom-set", path=path, property="rate", value="%g" % rate)
        if pulse:
            self.qmp("qom-set", path=path, property="pulse", value=True)
        return self.qmp("qom-get", path=path, property="count")

    def stop(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()


def boot(board, flash, workdir, name="node", hci=None, extra=(), **opts):
    """Boot flash (a merged image) on a board. UART0 goes to <workdir>/<name>.log, the
    USB serial (plugged in) to <name>-usb.log; hci: a TCP port for UART1 (a virtual
    Bluetooth controller), else nothing on it."""
    if board.startswith("kicad:"):
        machine, devices = from_kicad(board[len("kicad:"):], **opts)
    else:
        machine, devices = BOARDS[board](**opts)
    log = os.path.join(workdir, name + ".log")
    img = os.path.join(workdir, name + "-flash.bin")
    with open(flash, "rb") as src, open(img, "wb") as dst:
        data = src.read()                      # Each node its own flash (NVS: its identity)
        size = 4 << 20
        while size < len(data):
            size *= 2                          # QEMU wants the whole chip: 4, 8 or 16 MB
        dst.write(data + b"\xff" * (size - len(data)))
    args = [QEMU, "-machine", machine, "-display", "none", "-monitor", "none", "-nic", "none",
            "-drive", "file=%s,if=mtd,format=raw" % img,
            "-serial", "file:" + log,
            "-serial", "tcp:127.0.0.1:%d" % hci if hci else "null",
            "-serial", "null",
            "-serial", "file:" + log[:-4] + "-usb.log"]
    qmp = log[:-4] + "-qmp.sock"
    if len(qmp.encode()) >= 100:                   # Unix socket paths: under 108 bytes
        import tempfile
        qmp = os.path.join(tempfile.mkdtemp(prefix="qmp-"), name + ".sock")
    args += ["-qmp", "unix:%s,server=on,wait=off" % qmp]
    if QEMU_BIOS:
        args += ["-L", QEMU_BIOS]
    args += devices + list(extra)                  # e.g. ["-gdb", "tcp::1234"]
    with open(os.path.join(workdir, name + "-qemu.txt"), "w") as f:
        f.write(" ".join(args) + "\n")
    proc = subprocess.Popen(args, stdout=open(os.path.join(workdir, name + "-qemu.log"), "w"),
                            stderr=subprocess.STDOUT)
    return Node(name, proc, log, qmp)
