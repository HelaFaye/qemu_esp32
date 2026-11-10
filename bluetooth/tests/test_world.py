"""The virtual world on its own (no emulator): a virtual Switch finds a virtual Pro
Controller by inquiry, pairs, opens HID and talks to it through the modeled link."""
import asyncio
import struct
import time

import pytest

from vbt import LinkModel, World
from vbt.peers import VirtualProController, VirtualSwitch

jctool = pytest.importorskip("jctool.fake")


def subcommand(sub, args=b""):
    p = bytearray(49)
    p[0], p[10] = 0x01, sub
    p[11:11 + len(args)] = args
    return bytes(p)


async def reply(switch, sub, timeout=2.0):
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        r = await switch.receive(0.2)
        if r and r[0] == 0x21 and r[14] == sub:
            return r
    return None


async def scene(model=None, **kw):
    world = World(model)
    fake = jctool.FakeJoyCon(jctool.PROCON)
    pro = VirtualProController(world, fake)
    switch = VirtualSwitch(world)
    await pro.start()
    await switch.start()
    return world, fake, pro, switch


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 60))


def test_find_pair_and_talk():
    async def go():
        world, fake, pro, switch = await scene()
        address = await switch.find("Pro Controller")
        assert str(address).startswith("98:B6:E9:12:34:56")
        await switch.connect(address)
        switch.send(subcommand(0x02))
        r = await reply(switch, 0x02)
        assert r is not None and r[17] == 0x03                   # A Pro Controller
        switch.send(subcommand(0x10, struct.pack("<IB", 0x6050, 3)))
        r = await reply(switch, 0x10)
        assert r[20:23] == bytes(fake.spi[0x6050:0x6053])
        pro.stop()
        world.close()
    run(go())


def test_latency_model():
    """20ms each way: a subcommand's round trip takes at least 40ms."""
    async def go():
        world, fake, pro, switch = await scene(LinkModel(base_ms=20.0))
        await switch.connect(await switch.find())
        t = time.monotonic()
        switch.send(subcommand(0x02))
        assert await reply(switch, 0x02) is not None
        assert time.monotonic() - t >= 0.040
        pro.stop()
        world.close()
    run(go())


def test_retransmission_keeps_order_and_delivers_everything():
    async def go():
        world, fake, pro, switch = await scene(LinkModel(loss=0.3, retry_ms=2.0, seed=1))
        await switch.connect(await switch.find())
        for i in range(20):
            switch.send(subcommand(0x10, struct.pack("<IB", 0x6000 + i, 1)))
        got = []
        while len(got) < 20:
            r = await reply(switch, 0x10, 5.0)
            assert r is not None
            got.append(struct.unpack_from("<I", r, 15)[0])
        assert got == [0x6000 + i for i in range(20)]
        assert world.link.stats["retries"] > 0 and world.link.stats["flushed"] == 0
        pro.stop()
        world.close()
    run(go())


def test_outage_past_supervision_timeout_disconnects():
    async def go():
        model = LinkModel(supervision_timeout_ms=200.0)
        world, fake, pro, switch = await scene(model)
        await switch.connect(await switch.find())
        gone = asyncio.get_running_loop().create_future()
        switch.connection.on("disconnection", lambda reason: gone.done() or gone.set_result(reason))
        t = world.link._now_ms()
        model.outages = ((t, t + 10000),)
        switch.send(subcommand(0x02))
        reason = await asyncio.wait_for(gone, 5)
        assert reason == 0x08                                    # Connection timeout
        assert world.link.stats["lost_links"] >= 1
        pro.stop()
        world.close()
    run(go())
