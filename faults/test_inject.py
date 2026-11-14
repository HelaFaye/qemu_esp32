"""The injector against a fake GDB stub (no QEMU): framing, run control, faults."""
import os
import socket
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from inject import GDBRemote, Injector, insn_length, _unescape  # noqa: E402
from campaign import dest, _parse  # noqa: E402


class FakeStub(threading.Thread):
    """A one-CPU target in all-stop mode, as QEMU's stub behaves: stopped on connect,
    packets only while stopped, a breakpoint stops it the next time it's 'reached'."""

    def __init__(self):
        super().__init__(daemon=True)
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.regs = {0: 0x42000000, 69: 2}          # pc, windowbase
        self.regs.update({1 + i: 0x100 + i for i in range(64)})
        self.mem = bytearray(256)
        self.bps = set()
        self.running = False
        self.ignored = []
        self.log = []

    def reply(self, conn, payload):
        conn.sendall(GDBRemote.frame(payload))

    def run(self):
        conn, _ = self.srv.accept()
        buf = b""
        while True:
            data = conn.recv(4096)
            if not data:
                return
            buf += data
            while buf:
                if buf[:1] == b"+":
                    buf = buf[1:]
                elif buf[:1] == b"\x03":
                    buf = buf[1:]
                    self.running = False
                    self.reply(conn, "T02thread:p01.01;")
                elif buf[:1] == b"$":
                    j = buf.find(b"#")
                    if j < 0 or len(buf) < j + 3:
                        break
                    p, buf = buf[1:j].decode(), buf[j + 3:]
                    self.log.append(p)
                    if self.running:
                        self.ignored.append(p)        # QEMU ignores packets while running
                        continue
                    self.handle(conn, p)
                else:
                    buf = buf[1:]

    def handle(self, conn, p):
        r = ""
        if p.startswith("qSupported"):
            r = "PacketSize=1000;vContSupported+;multiprocess+"
        elif p == "QStartNoAckMode" or p.startswith("Hg") or p == "D":
            r = "OK"
        elif p == "?":
            r = "T05thread:p01.01;"
        elif p.startswith("Z0,"):
            self.bps.add(int(p.split(",")[1], 16))
            r = "OK"
        elif p.startswith("z0,"):
            self.bps.discard(int(p.split(",")[1], 16))
            r = "OK"
        elif p == "vCont;c":
            if self.bps:                               # "Reaches" the lowest breakpoint
                self.regs[0] = min(self.bps)
                r = "T05thread:p01.01;"
            else:
                self.running = True
                return
        elif p.startswith("vCont;s"):
            self.regs[0] += 3
            self.regs[1 + (self.regs[69] * 4 + 2) % 64] = 0xF0      # The insn writes a2
            r = "T05thread:p01.01;"
        elif p.startswith("p"):
            r = (self.regs.get(int(p[1:], 16), 0)).to_bytes(4, "little").hex()
        elif p.startswith("P"):
            n, v = p[1:].split("=")
            self.regs[int(n, 16)] = int.from_bytes(bytes.fromhex(v), "little")
            r = "OK"
        elif p.startswith("m"):
            a, n = (int(x, 16) for x in p[1:].split(","))
            r = self.mem[a:a + n].hex()
        elif p.startswith("M"):
            head, data = p[1:].split(":")
            a, n = (int(x, 16) for x in head.split(","))
            self.mem[a:a + n] = bytes.fromhex(data)
            r = "OK"
        self.reply(conn, r)


def stub_and_injector():
    s = FakeStub()
    s.start()
    return s, Injector(s.port)


def test_framing_and_escapes():
    assert GDBRemote.frame("OK") == b"$OK#9a"
    assert _unescape(b"}\x03") == b"#"
    assert _unescape(b"0* ") == b"0000"          # '0' then 3 more ( ' ' = 32 - 29 )


def test_insn_length():
    assert insn_length(0x1D) == 2          # retw.n (op0 0xD)
    assert insn_length(0xA8) == 2          # l32i.n (op0 0x8)
    assert insn_length(0x36) == 3          # entry (op0 0x6)


def test_never_talks_to_a_running_target():
    s, inj = stub_and_injector()
    assert inj.running
    assert inj.read_register("pc") == 0x42000000
    assert not s.ignored


def test_windowed_register_names():
    s, inj = stub_and_injector()
    # windowbase 2: a3 is physical ar11, gdb register 12
    assert inj.read_register("a3") == 0x100 + 11
    assert inj.read_register("ar3") == 0x103


def test_skip_moves_past_the_instruction():
    s, inj = stub_and_injector()
    s.mem[0:1] = b"\x00"
    assert inj.skip(0x42001000, length=2)
    assert s.regs[0] == 0x42001002
    assert not s.bps and inj.running and not s.ignored


def test_flip_register_after_steps_first():
    s, inj = stub_and_injector()
    assert inj.flip_register_after(0x42001000, "a2", bit=0)
    assert s.regs[0] == 0x42001003
    assert s.regs[1 + (2 * 4 + 2) % 64] == 0xF1      # The insn's 0xF0, bit 0 flipped


def test_flip_memory_now():
    s, inj = stub_and_injector()
    s.mem[0x10] = 0x0F
    assert inj.flip_memory(0x10, bit=7)
    assert s.mem[0x10] == 0x8F and not s.ignored


def test_destination_registers():
    insns = _parse("420011ef:\t20a0a0  \tnsau\ta2, a10\n"
                   "42001201:\t00ca16  \tbeqz\ta10, 42001211 <attempt_plain+0x19>\n"
                   "42001206:\t0129    \ts32i.n\ta2, a1, 0\n")
    assert [i["len"] for i in insns] == [3, 3, 2]
    assert [dest(i) for i in insns] == ["a2", None, None]
