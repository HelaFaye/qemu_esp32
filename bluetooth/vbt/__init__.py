"""vbt: virtual Bluetooth for ESP32 firmware under QEMU (and for host-side tests).

The firmware's real Bluetooth host stack (each ESP-IDF version's Bluedroid or NimBLE) runs
in QEMU and talks standard HCI over a serial channel (esp_qemu_hci); here a virtual
controller (Bumble's) answers on the other end, on a virtual radio link shared with
virtual peers: a Switch, a Pro Controller, any Bumble device. The link has a model:
latency, baseband retransmission, flush and supervision timeouts.
"""
from .link import LinkModel, ModeledLink
from .world import World

__all__ = ["LinkModel", "ModeledLink", "World"]
