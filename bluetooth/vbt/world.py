"""A virtual radio world: one modeled link, the controllers on it (a firmware's in QEMU,
over its HCI serial channel, or in-process ones for virtual peers)."""
import asyncio
import logging

from bumble.device import Device, DeviceConfiguration
from bumble.hci import Address
from bumble.host import Host
from bumble.transport import open_transport
from bumble.transport.common import AsyncPipeSink

from .controller import PROFILES, ChipController
from .link import LinkModel, ModeledLink

logger = logging.getLogger(__name__)


class World:
    def __init__(self, model=None):
        self.link = ModeledLink(model or LinkModel.ideal())
        self.transports = []
        self.controllers = []

    async def attach_hci(self, spec, address=None, profile="esp32", name=None):
        """A controller for a host stack elsewhere: QEMU's HCI serial channel
        (spec "tcp-client:127.0.0.1:PORT"), or any Bumble transport ("pty:", "serial:"...).
        `address`: the controller's public address (the firmware's Bluetooth address)."""
        transport = await open_transport(spec)
        self.transports.append(transport)
        c = ChipController(name or "hci%d" % len(self.controllers), profile=PROFILES.get(profile, profile),
                           host_source=transport.source, host_sink=transport.sink, link=self.link,
                           public_address=address)
        self.controllers.append(c)
        return c

    def add_device(self, name, address, class_of_device=0, profile="generic", power_on=True):
        """An in-process Bumble device on the link (a virtual peer). Returns the Device; call
        `await device.power_on()` (or pass power_on and await world.ready())."""
        c = ChipController(name, profile=PROFILES.get(profile, profile), link=self.link, public_address=address)
        self.controllers.append(c)
        config = DeviceConfiguration(name=name, address=Address(address), classic_enabled=True,
                                     class_of_device=class_of_device)
        return Device(name=name, address=Address(address), config=config, host=Host(c, AsyncPipeSink(c)))

    def close(self):
        for t in self.transports:
            try:
                t.close()
            except Exception:                 # Already gone with the emulator
                pass
