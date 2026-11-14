"""Fault injection on a running emulated ESP32: what a voltage or EM glitch does to the
firmware, injected precisely and repeatably.

QEMU's GDB stub (-gdb tcp::PORT, see boards.boot(extra=...)) is the way in: breakpoints
pick the moment (an instruction address, an occurrence of it), and register and memory
access make the fault. Nothing here needs a debugger or a patched QEMU.

    from faults.inject import Injector
    node = boot("meshcore-bare", flash, work, extra=["-gdb", "tcp::1234"])
    inj = Injector(1234)
    inj.skip(0x42001234)                     # The next time it gets there, that instruction doesn't run
    inj.flip_register_after(0x42001240, "a2", bit=0)
    inj.flip_memory(0x3fc90000, bit=7)       # Now
    inj.close()

Fault models (docs/stress-testing.md, 3 and 4):
- skip: an instruction not executed (the classic glitch result: a branch not taken, a
  store not done, a call not made).
- flip_register_after: an instruction executes and its result (or any register) comes out
  with a bit flipped: a corrupted load or compare.
- flip_memory / set_memory: a bit in SRAM, a peripheral register, a variable.
- brown-out: Node.qmp("system_reset") (the whole chip resets, as below the BOD level).

Registers by name: pc, a0-a15 (the current window), ar0-ar63 (physical), sar,
windowbase, windowstart, ps. The numbers are QEMU's gdb register map for the ESP32/S3
(target/xtensa/core-esp32s3/gdb-config.inc.c): pc 0, ar0-63 1-64, sar 68, windowbase 69,
windowstart 70, ps 73. The classic ESP32 uses the same order.
"""
import re
import socket
import time

REGS = {"pc": 0, "sar": 68, "windowbase": 69, "windowstart": 70, "ps": 73}
REGS.update({"ar%d" % i: 1 + i for i in range(64)})
NAREG = 64


class RSPError(RuntimeError):
    pass


class Stop:
    """Why the target stopped: a signal and the CPU (gdb thread) that stopped it."""

    def __init__(self, reply):
        self.reply = reply
        self.signal = int(reply[1:3], 16) if reply[:1] in ("S", "T") else None
        m = re.search(r"thread:([^;]+);", reply)
        self.thread = m.group(1) if m else None
        self.exited = reply[:1] in ("W", "X")

    def __repr__(self):
        return "Stop(%r)" % self.reply


def insn_length(first_byte):
    """An Xtensa instruction's length from its first byte (little-endian op0): the density
    (narrow) instructions are 2 bytes, the rest 3. The S3's 4-byte TIE (ee.*) instructions
    aren't told apart here; pass length= to skip() for those."""
    op0 = first_byte & 0x0F
    return 2 if 8 <= op0 <= 13 else 3


class GDBRemote:
    """The GDB remote serial protocol, client side, as much as fault injection needs."""

    def __init__(self, port, host="127.0.0.1", timeout=10.0, connect_timeout=10.0):
        end = time.time() + connect_timeout
        while True:
            try:
                self.sock = socket.create_connection((host, port), timeout=timeout)
                break
            except OSError:
                if time.time() > end:
                    raise
                time.sleep(0.1)
        self.buf = b""
        self.no_ack = False
        self.packet("qSupported:multiprocess+;vContSupported+")
        if self.packet("QStartNoAckMode") == "OK":
            self.no_ack = True

    @staticmethod
    def frame(payload):
        data = payload.encode("latin-1")
        return b"$" + data + b"#%02x" % (sum(data) & 0xFF)

    def send(self, payload):
        self.sock.sendall(self.frame(payload))

    def _fill(self, timeout):
        self.sock.settimeout(timeout)
        try:
            data = self.sock.recv(65536)
        except socket.timeout:
            return False
        if not data:
            raise RSPError("the GDB stub closed the connection (QEMU exited?)")
        self.buf += data
        return True

    def receive(self, timeout=10.0):
        """The next packet's payload; None on timeout."""
        end = time.time() + timeout
        while True:
            self.buf = self.buf.lstrip(b"+")
            i = self.buf.find(b"$")
            if i >= 0:
                j = self.buf.find(b"#", i)
                if j >= 0 and len(self.buf) >= j + 3:
                    payload = self.buf[i + 1:j]
                    self.buf = self.buf[j + 3:]
                    if not self.no_ack:
                        self.sock.sendall(b"+")
                    return _unescape(payload).decode("latin-1")
            left = end - time.time()
            if left <= 0 or not self._fill(left):
                return None

    def packet(self, payload, timeout=10.0):
        self.send(payload)
        r = self.receive(timeout)
        if r is None:
            raise RSPError("no reply to %r" % payload)
        return r

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def _unescape(data):
    out, i = bytearray(), 0
    while i < len(data):
        c = data[i]
        if c == 0x7D and i + 1 < len(data):         # '}' escapes the next byte (xor 0x20)
            out.append(data[i + 1] ^ 0x20)
            i += 2
        elif c == 0x2A and out and i + 1 < len(data):   # '*' run-length encoding
            out += out[-1:] * (data[i + 1] - 29)
            i += 2
        else:
            out.append(c)
            i += 1
    return bytes(out)


class Injector(GDBRemote):
    """Faults on one emulated chip. The target runs between calls; each call stops it,
    waits for the moment, makes the fault and lets it run on."""

    def __init__(self, port, **kw):
        super().__init__(port, **kw)
        self.thread = None          # The CPU the last stop was on
        self.running = False        # QEMU stops the machine when a debugger connects, and
        st = Stop(self.packet("?"))  # its stub takes packets only while it's stopped
        self.thread = st.thread
        self.cont()

    # -- Run control ------------------------------------------------------------------

    def halt(self, timeout=10.0):
        """Stop every CPU now (a fault 'at this time')."""
        self.sock.sendall(b"\x03")
        r = self.receive(timeout)
        if r is None:
            raise RSPError("the target didn't stop")
        st = Stop(r)
        self.thread = st.thread
        self.running = False
        return st

    def stopped(self):
        """Make sure it's stopped (the stub ignores everything else while it runs)."""
        if self.running:
            self.halt()

    def cont(self):
        self.send("vCont;c")
        self.running = True

    def wait(self, timeout):
        """Wait for a stop after cont(); None if it's still running at the timeout."""
        r = self.receive(timeout)
        if r is None:
            return None
        st = Stop(r)
        self.running = False
        if st.exited:
            raise RSPError("the target exited: %s" % r)
        self.thread = st.thread
        return st

    def step(self):
        """One instruction on the CPU that stopped last."""
        self.send("vCont;s:%s" % self.thread if self.thread else "s")
        self.running = True
        st = self.wait(10.0)
        if st is None:
            raise RSPError("single step didn't come back")
        return st

    def _select(self):
        if self.thread:
            self.packet("Hg" + self.thread)

    def run_to(self, addr, count=1, timeout=30.0):
        """Run until the count-th time any CPU reaches addr; it stops there (the
        instruction not yet run). Returns the Stop, or None after timeout (then the
        target is halted and the breakpoint gone)."""
        self.stopped()
        self._ok(self.packet("Z0,%x,4" % addr), "set a breakpoint at %#x" % addr)
        end = time.time() + timeout
        st = None
        try:
            for _ in range(count):
                self.cont()
                st = self.wait(max(0.0, end - time.time()))
                if st is None:
                    self.halt()
                    return None
        finally:
            self.packet("z0,%x,4" % addr)
        return st

    # -- Registers and memory ---------------------------------------------------------

    def _regnum(self, name):
        if isinstance(name, int):
            return name
        if name in REGS:
            return REGS[name]
        m = re.fullmatch(r"a(\d+)", name)
        if m and int(m.group(1)) < 16:
            wb = self.read_register("windowbase")
            return 1 + (wb * 4 + int(m.group(1))) % NAREG
        raise KeyError("unknown register %r" % name)

    def read_register(self, name):
        self.stopped()
        self._select()
        r = self.packet("p%x" % self._regnum(name))
        if r.startswith("E") or not r:
            raise RSPError("can't read %s: %s" % (name, r))
        return int.from_bytes(bytes.fromhex(r), "little")

    def write_register(self, name, value):
        self.stopped()
        n = self._regnum(name)
        self._select()
        self._ok(self.packet("P%x=%s" % (n, (value & 0xFFFFFFFF).to_bytes(4, "little").hex())),
                 "write %s" % name)

    def read_memory(self, addr, length):
        self.stopped()
        r = self.packet("m%x,%x" % (addr, length))
        if r.startswith("E") or len(r) != 2 * length:
            raise RSPError("can't read %d bytes at %#x: %s" % (length, addr, r))
        return bytes.fromhex(r)

    def write_memory(self, addr, data):
        self.stopped()
        self._ok(self.packet("M%x,%x:%s" % (addr, len(data), data.hex())), "write %#x" % addr)

    @staticmethod
    def _ok(reply, what):
        if reply != "OK":
            raise RSPError("couldn't %s: %r" % (what, reply))

    # -- Faults -----------------------------------------------------------------------

    def skip(self, addr, count=1, length=None, timeout=30.0):
        """The count-th time execution reaches addr, that instruction is skipped. True if
        it was reached (and skipped)."""
        if self.run_to(addr, count, timeout) is None:
            self.cont()
            return False
        if length is None:
            length = insn_length(self.read_memory(addr, 1)[0])
        self.write_register("pc", addr + length)
        self.cont()
        return True

    def flip_register_after(self, addr, reg, bit=0, count=1, timeout=30.0):
        """The count-th time the instruction at addr runs, flip a bit of reg right after it
        (its result, if reg is what it writes). True if it was reached."""
        if self.run_to(addr, count, timeout) is None:
            self.cont()
            return False
        self.step()
        self.write_register(reg, self.read_register(reg) ^ (1 << bit))
        self.cont()
        return True

    def set_register_after(self, addr, reg, value, count=1, timeout=30.0):
        """As flip_register_after, but reg is set to value (stuck-at, a zeroed load)."""
        if self.run_to(addr, count, timeout) is None:
            self.cont()
            return False
        self.step()
        self.write_register(reg, value)
        self.cont()
        return True

    def flip_memory(self, mem, bit=0, at=None, count=1, timeout=30.0):
        """Flip one bit of the byte at mem: now, or when execution reaches at."""
        if at is not None:
            if self.run_to(at, count, timeout) is None:
                self.cont()
                return False
        else:
            self.stopped()
        b = self.read_memory(mem, 1)[0] ^ (1 << bit)
        self.write_memory(mem, bytes([b]))
        self.cont()
        return True

    def set_memory(self, mem, data, at=None, count=1, timeout=30.0):
        if at is not None:
            if self.run_to(at, count, timeout) is None:
                self.cont()
                return False
        else:
            self.stopped()
        self.write_memory(mem, data)
        self.cont()
        return True

    def close(self):
        """Leave it running, without us."""
        try:
            self.stopped()
            self.send("D")
            self.receive(2.0)
        except (RSPError, OSError):
            pass
        super().close()
