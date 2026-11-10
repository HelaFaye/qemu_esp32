"""A virtual MeshCore companion app: a phone on vbt's virtual radio that finds a node by its
companion service, connects over LE, pairs as the app does (passkey 123456 typed in, MITM,
legacy pairing: the node displays it), and talks the companion protocol: one command per
write to RX, responses as TX notifications.

The node's own NimBLE host (in QEMU) runs against a virtual controller (vbt) on UART1.
"""
import asyncio
import struct
import time

from bumble.core import PhysicalTransport, UUID
from bumble.device import Peer
from bumble.pairing import PairingConfig, PairingDelegate

SERVICE = UUID("6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
RX = UUID("6E400002-B5A3-F393-E0A9-E50E24DCCA9E")      # Phone to node (write)
TX = UUID("6E400003-B5A3-F393-E0A9-E50E24DCCA9E")      # Node to phone (notify)
PASSKEY = 123456

CMD_APP_START = 1
CMD_GET_CONTACTS = 4
CMD_GET_DEVICE_TIME = 5
CMD_SET_DEVICE_TIME = 6
CMD_SEND_SELF_ADVERT = 7
RESP_OK = 0


class Typist(PairingDelegate):
    """A person typing the passkey the node shows."""

    def __init__(self, passkey=PASSKEY):
        super().__init__(io_capability=PairingDelegate.IoCapability.KEYBOARD_INPUT_ONLY)
        self.passkey = passkey

    async def get_number(self):
        return self.passkey

    async def confirm(self, auto=False):
        return True


class Phone:
    def __init__(self, world, address, name="Phone"):
        self.device = world.add_device(name, address)
        self.device.pairing_config_factory = lambda connection: PairingConfig(
            sc=False, mitm=True, bonding=True, delegate=Typist())
        self.connection = self.peer = self.rx = self.tx = None
        self.responses = asyncio.Queue()

    async def start(self):
        await self.device.power_on()

    async def find(self, timeout=30.0, exclude=()):
        """LE scanning until a node advertises the companion service: its address (not one
        in exclude: nodes keep advertising while connected)."""
        found = asyncio.get_running_loop().create_future()

        def on_adv(adv):
            uuids = adv.data.get(adv.data.COMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS) or \
                adv.data.get(adv.data.INCOMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS) or []
            if not found.done() and SERVICE in uuids and str(adv.address) not in exclude:
                found.set_result(adv.address)
        self.device.on("advertisement", on_adv)
        await self.device.start_scanning(filter_duplicates=True)
        try:
            return await asyncio.wait_for(found, timeout)
        finally:
            self.device.remove_listener("advertisement", on_adv)
            await self.device.stop_scanning()

    async def connect(self, address):
        self.connection = await self.device.connect(address, transport=PhysicalTransport.LE)
        await self.connection.pair()
        self.peer = Peer(self.connection)
        await self.peer.discover_services([SERVICE])
        service = self.peer.get_services_by_uuid(SERVICE)[0]
        await service.discover_characteristics()
        self.rx = service.get_characteristics_by_uuid(RX)[0]
        self.tx = service.get_characteristics_by_uuid(TX)[0]
        await self.peer.subscribe(self.tx, lambda value: self.responses.put_nowait(bytes(value)))

    async def command(self, code, args=b"", timeout=10.0):
        """A command, and the node's response frame (its code first)."""
        while not self.responses.empty():
            self.responses.get_nowait()
        await self.rx.write_value(bytes([code]) + bytes(args), with_response=True)
        return await asyncio.wait_for(self.responses.get(), timeout)

    async def set_time(self, t=None):
        t = int(time.time()) if t is None else t
        r = await self.command(CMD_SET_DEVICE_TIME, struct.pack("<I", t))
        return r[:1] == bytes([RESP_OK])
