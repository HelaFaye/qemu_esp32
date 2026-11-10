# ESP32 Bluetooth under QEMU

Espressif's QEMU emulates no radio on any chip: Bluetooth, Wi-Fi and ESP-NOW firmware
can't be tested in it. This folder fills the Bluetooth part, in a way that keeps what
matters exact:

- **The host stack runs for real.** Each ESP-IDF version's own Bluedroid (or NimBLE), and
  the firmware's own Bluetooth code, run in QEMU unchanged. Only the controller (the closed
  binary that drives the radio) is replaced: the host talks standard HCI to it, over a
  serial port.
- **The controller is virtual.** Here on the PC, [Bumble](https://github.com/google/bumble)'s
  controller with an ESP32 chip profile answers on that serial port. It sits on a virtual
  radio link with other virtual devices: a Switch, a Pro Controller, any Bumble device.
- **The radio link has a model.** Classic Bluetooth retransmits lost packets in the radio
  itself, so a host sees late packets, never lost ones. The model gives latency and
  jitter, retransmission delay (in order), a flush timeout (late packets dropped), the
  supervision timeout (a link that gets nothing through disconnects both ends) and outage
  windows.

Not tied to one project: firmware whose Bluetooth controller QEMU lacks runs its own host
stack against it (BlueCubeMod was the first; meshcore-bare-esp32s3, gbagate and others next).

## Pieces

| | what it is |
|---|---|
| `esp_qemu_hci/` | ESP-IDF component: Bluedroid (host-only) over HCI H4 on a UART |
| `vbt/link.py` | The modeled radio link (`LinkModel`, `ModeledLink`) |
| `vbt/controller.py` | The virtual controller with a chip profile (`ChipController`) |
| `vbt/world.py` | A world: the link, controllers for firmware in QEMU and for virtual peers |
| `vbt/peers.py` | A virtual Switch (HID host) and a virtual Pro Controller (HID device) |
| `tests/` | The virtual world on its own: inquiry, pairing, HID, the link model |

## Using it in a firmware

1. Build a QEMU variant of the firmware with no controller and the host stack on a UART:
   - Bluedroid: `CONFIG_BT_CONTROLLER_DISABLED=y`, add this folder's `esp_qemu_hci` to
     `EXTRA_COMPONENT_DIRS`, and call `esp_qemu_hci_init()` before `esp_bluedroid_init()`
     (in place of `esp_bt_controller_init()` / `_enable()`).
   - NimBLE (BLE; the ESP32-S3, C3, C6): no code. `CONFIG_BT_CONTROLLER_DISABLED=y`,
     `CONFIG_BT_NIMBLE_TRANSPORT_UART=y` and the UART's port: NimBLE's own UART transport.
2. Run QEMU with that UART on a socket: QEMU connects the chip's UARTs to its `-serial`
   options in order (`-serial file:console.log -serial tcp:...:LINK -serial tcp:...:HCI,server=on,wait=off`).
3. On the PC: `World().attach_hci("tcp-client:127.0.0.1:HCI", address=...)`, then peers on
   the same world.

The controller's Bluetooth address is the virtual controller's, not the firmware's:
the firmware has to announce the one it wants (BlueCubeMod does it on its test link) and
wait until the controller is attached before starting Bluetooth.

## What is exact and what is modeled

| exact (real code) | modeled |
|---|---|
| The host stack: SDP, L2CAP, pairing, HID, GAP | The controller's timing and buffers (a chip profile) |
| The firmware's Bluetooth code | The radio: latency, retransmission, flush, link loss |
| The peers' protocols (Bumble's stack) | What a Switch does, as far as public research tells |

The ESP32 chip profile's values (what its controller reports, its buffer count and size)
must be captured on a real board for each ESP-IDF version: until then they are Bumble's
defaults with Espressif's company ID, and `ChipProfile.measured` says so. So are the link
model's numbers for a real board and setting (sniff intervals, typical latency).

## Next

- QEMU: serial channels beyond the chip's three UARTs (so the HCI channel doesn't take
  one the firmware uses), transmit paced at the baud rate, RTS/CTS: to contribute to
  [espressif/qemu](https://github.com/espressif/qemu).
- Bumble: inquiry and Classic encryption in its own controller (done here in a subclass),
  to contribute upstream.
- Chip profiles from hardware captures; a virtual Joy-Con; LE for the ESP32-S3.
