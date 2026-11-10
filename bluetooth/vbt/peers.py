"""Virtual peers: a Switch (a HID host that finds, connects to and drives a Pro Controller)
and a Pro Controller / Joy-Con (a HID device answering from a controller model)."""
import asyncio
import collections
import logging

from bumble import hid
from bumble.core import PhysicalTransport

logger = logging.getLogger(__name__)

PRO_CONTROLLER_COD = 0x002508
SWITCH_COD = 0x00010C          # A Switch reports itself as a computer (major 1)


class VirtualSwitch:
    """A HID host like a Switch: inquiry for a "Pro Controller", page it, open the HID
    control and interrupt channels, then output reports out and input reports in. Its
    reads and writes look like a hidapi device (`write(data)`, `read(size, timeout_ms)`),
    so Joy-Con Toolkit's code runs on top of it."""

    def __init__(self, world, address="98:B6:E9:00:00:01", name="Nintendo Switch"):
        self.device = world.add_device(name, address, class_of_device=SWITCH_COD)
        self.host = hid.Host(self.device)
        self.inputs = collections.deque(maxlen=64)
        self.arrived = asyncio.Event()
        self.host.on(self.host.EVENT_INTERRUPT_DATA, self._on_interrupt)
        self.connection = None

    def _on_interrupt(self, pdu):
        # HIDP DATA | Input: 0xA1, then the report (its ID first)
        if pdu and pdu[0] == 0xA1:
            self.inputs.append(bytes(pdu[1:]))
            self.arrived.set()

    async def start(self):
        await self.device.power_on()

    async def find(self, name="Pro Controller", timeout=10.0):
        """Inquiry until a device with this name answers: its address."""
        found = asyncio.get_running_loop().create_future()

        def on_result(address, class_of_device, data, rssi):
            n = data.get(data.COMPLETE_LOCAL_NAME, raw=False) if data else None
            if not found.done() and (n == name or name is None):
                found.set_result(address)
        self.device.on("inquiry_result", on_result)
        await self.device.start_discovery()
        try:
            return await asyncio.wait_for(found, timeout)
        finally:
            self.device.remove_listener("inquiry_result", on_result)
            await self.device.stop_discovery()

    async def connect(self, address, pair=True):
        self.connection = await self.device.connect(address, transport=PhysicalTransport.BR_EDR)
        if pair:
            await self.connection.authenticate()
            await self.connection.encrypt()
        await self.host.connect_control_channel()
        await self.host.connect_interrupt_channel()
        return self.connection

    async def disconnect(self):
        if self.connection:
            await self.connection.disconnect()
            self.connection = None

    # hidapi-like, async
    def send(self, report):
        self.host.send_data(bytes(report))

    async def receive(self, timeout=1.0):
        if not self.inputs:
            self.arrived.clear()
            try:
                await asyncio.wait_for(self.arrived.wait(), timeout)
            except asyncio.TimeoutError:
                return b""
        return self.inputs.popleft() if self.inputs else b""


class VirtualProController:
    """A HID device like a Pro Controller (discoverable as "Pro Controller", class 0x002508)
    whose reports come from a controller model with a hidapi-like interface: `write(data)`
    for each output report, `read(size, timeout_ms)` polled for input reports (Joy-Con
    Toolkit's emulated controller, BlueCubeMod's core...)."""

    def __init__(self, world, model, address="98:B6:E9:12:34:56", name="Pro Controller", poll_ms=1.0):
        self.device = world.add_device(name, address, class_of_device=PRO_CONTROLLER_COD)
        self.hid = hid.Device(self.device)
        self.model = model
        self.poll_ms = poll_ms
        self.hid.on(self.hid.EVENT_INTERRUPT_DATA, self._on_interrupt)
        self.task = None

    def _on_interrupt(self, pdu):
        if pdu and pdu[0] == 0xA2:                   # HIDP DATA | Output
            self.model.write(bytes(pdu[1:]))

    async def start(self):
        await self.device.power_on()
        await self.device.set_discoverable(True)
        await self.device.set_connectable(True)
        self.task = asyncio.get_running_loop().create_task(self._pump())

    async def _pump(self):
        while True:
            r = self.model.read(64, 0)
            if r and self.hid.l2cap_intr_channel is not None:
                self.hid.send_data(bytes(r))
            else:
                await asyncio.sleep(self.poll_ms / 1000.0)

    def stop(self):
        if self.task:
            self.task.cancel()
