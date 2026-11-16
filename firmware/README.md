# Test cases: real firmware on emulated boards

Each directory is a test case for the emulation: a real project's firmware, built from its
own repository at a pinned commit (`COMMIT`), booted on the board it was made for
(`boards.py`), and checked by what it does: what it prints, what it shows, what it sends.
A test case confirms the emulated features its firmware uses; when one fails, the
emulation is wrong or missing something, and the failure says where. These projects aren't
maintained here; they were picked because they use the peripherals the emulation covers.
CI: `.github/workflows/firmware.yml`.

`boards.py` is also for your own boards: see [docs/boards.md](../docs/boards.md); the device
tests and CI: [docs/testing.md](../docs/testing.md).

| Firmware | Board | Scenario |
|---|---|---|
| [meshcore-bare-esp32s3](https://github.com/mytechnotalent/meshcore-bare-esp32s3) | ESP32-S3 + SX1262 | `meshcore-bare/two_nodes.py`: two nodes on one air (`radio/airhub.py`) bring their radios up, advertise, hear each other and store each other as contacts |
| [LilyGO T-Deck](https://github.com/Xinyuan-LilyGO/T-Deck) factory firmware | T-Deck (ST7789, GT911, keyboard, SX1262, SD, trackball, 8 MB PSRAM) | `t-deck/boot.py`: it finds its display, touch, keyboard and radio, starts Wi-Fi and draws its UI (captured from the screen); in its loop, "hello" typed on the keyboard reaches it, its radio sends, and its screen answers |
| [LilyGO T-Display-S3](https://github.com/Xinyuan-LilyGO/T-Display-S3) factory firmware | T-Display-S3 (ST7789 on LCD_CAM's 8-bit bus, 8 MB octal PSRAM, two buttons) | `t-display-s3/check.py`: its boot animation reaches the glass; Wi-Fi starts and scans, it waits out its connect and SmartConfig timeouts (~3 minutes) and carries on without network; its clock UI comes up, and KEY turns to its next page |
| [SlimeVR tracker](https://github.com/HelaFaye/SlimeVR-Tracker-ESP) (its BOARD_ESP32S3_SUPERMINI build on Arduino-ESP32 3.3, with the emulated network's credentials) | ESP32-S3 SuperMini | `slimevr/check.py`: it boots and hashes its own image, sends its LED's frames through the RMT, formats LittleFS and saves its configuration, goes on with no IMU (none is emulated yet), joins "Open Wifi", finds a stand-in server on the host by its broadcast handshake, and stays connected on its heartbeats |
| [wifi-start](wifi-start) (here: a minimal ESP-IDF app) | DevKitC | `wifi-start/check.py`, twice. With no network: Wi-Fi initializes, the PHY calibrates, the station starts, a scan finds nothing. With an access point: the scan lists it, the station connects, gets an address by DHCP, and a UDP round trip with an echo server on the host comes back |
| [gbagate](https://github.com/HelaFaye/gbagate) | its touch carrier (ILI9341 + XPT2046, micro SD, MCP23017s), its devkit (ST7735, SD, ten switches) and its Supermini (ST7735, SD, switches on an MCP23017) | `gbagate/check.sh`, gbagate's own `qemu/accurate_boot.py`: the panel, touch, card and link PHYs come up; the picture is read back from the glass (the UI at 15/8, the touch keys, the theme's colours); a touch lights its key and moves the menu; touch alone reaches Send Program, which lists the program on the card. `gbagate/boards_check.py`, on each full board from `boards.py`: every task starts, the card mounts, a button press (on the Supermini, through its expander) changes the screen |

## Emulator builds

Where a board has something QEMU can't be, an emulator build changes only that, in
the firmware's own configuration. The ESP32-S3's Bluetooth controller is radio hardware
inside the chip; its emulator build uses ESP-IDF's host-only Bluetooth (NimBLE or
Bluedroid over HCI on UART1), so the firmware's real Bluetooth host talks to a virtual
controller (`../bluetooth`) instead. The stock build is booted too, to show how far it
gets without one.

## Running locally

```
patches/espressif-qemu/build.sh ~/esp-qemu
export QEMU_XTENSA=~/esp-qemu/build/qemu-system-xtensa
firmware/meshcore-bare/build.sh /tmp/mcb                  # PlatformIO, esptool
firmware/meshcore-bare/two_nodes.py /tmp/mcb/emulator/flash.bin /tmp/mcb/run
```
