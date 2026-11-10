"""The virtual phone against an in-process stand-in for the node (a Bumble peripheral with
the companion service, encrypted and authenticated as the firmware's, DisplayOnly with a
static passkey), on vbt's virtual radio: finding, pairing and the command round trip work
before the real firmware is in the loop.

    python3 firmware/meshcore-bare/test_phone.py      (needs bumble, bluetooth/ on the path)
"""
import asyncio
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "bluetooth"))

from bumble.core import AdvertisingData  # noqa: E402
from bumble.gatt import Characteristic, CharacteristicValue, Service  # noqa: E402
from bumble.pairing import PairingConfig, PairingDelegate  # noqa: E402

from phone import PASSKEY, RX, SERVICE, TX, Phone  # noqa: E402
from vbt import World  # noqa: E402


class Display(PairingDelegate):
    def __init__(self):
        super().__init__(io_capability=PairingDelegate.IoCapability.DISPLAY_OUTPUT_ONLY)

    async def generate_passkey(self):
        return PASSKEY


async def main():
    world = World()
    node = world.add_device("MeshCore-node", "24:0A:C4:00:00:01")
    node.pairing_config_factory = lambda c: PairingConfig(sc=False, mitm=True, bonding=True, delegate=Display())
    state = {"time": 0}

    def on_write(connection, value):
        value = bytes(value)
        if value[0] == 6 and len(value) >= 5:
            state["time"] = struct.unpack_from("<I", value, 1)[0]
            asyncio.get_running_loop().call_soon(
                lambda: asyncio.ensure_future(node.notify_subscribers(tx, b"\x00")))

    rx = Characteristic(RX, Characteristic.Properties.WRITE,
                        Characteristic.Permissions.WRITE_REQUIRES_AUTHENTICATION,
                        CharacteristicValue(write=on_write))
    tx = Characteristic(TX, Characteristic.Properties.READ | Characteristic.Properties.NOTIFY,
                        Characteristic.Permissions.READ_REQUIRES_AUTHENTICATION, b"")
    node.add_service(Service(SERVICE, [rx, tx]))
    node.advertising_data = bytes(AdvertisingData([
        (AdvertisingData.FLAGS, bytes([0x06])),
        (AdvertisingData.COMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS, bytes(SERVICE.to_bytes())),
    ]))
    await node.power_on()
    await node.start_advertising(auto_restart=True)

    phone = Phone(world, "AA:BB:CC:00:00:01")
    await phone.start()
    addr = await phone.find(timeout=10)
    print("found", addr)
    await phone.connect(addr)
    print("paired; encrypted:", phone.connection.is_encrypted)
    ok = await phone.set_time(1_800_000_000)
    print("set time:", ok, state["time"])
    assert ok and state["time"] == 1_800_000_000
    print("PASS")


if __name__ == "__main__":
    asyncio.run(main())
