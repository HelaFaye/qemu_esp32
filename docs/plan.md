# Plan

What comes next for the emulated boards, in order. Each item is done when real firmware
runs it as a test case in CI (`firmware/`), with qtests for the device models it needed
([testing](testing.md)).

## 1. gbagate's boards

| Item | State |
|---|---|
| devkit, supermini, carrier-touch: tasks, card, screen, inputs (`firmware/gbagate/boards_check.py`) | Done |
| carrier-knob | Waiting on gbagate's knob carrier image |
| ESP-NOW between two gbagate nodes: its current Wireless Adapter (RFU, GB-Link mode 0x07) peer path | To do: Wi-Fi frames between QEMUs (section 3, "A shared 2.4 GHz air") |
| The AGB-015 radio path, once gbagate has it | Section 3 |

## 2. SlimeVR-Tracker-ESP

| Item | State |
|---|---|
| Boot, RMT LED, LittleFS, Wi-Fi, UDP discovery and session (`firmware/slimevr/check.py`) | Done |
| Two ICM-45686s: found, calibrated at rest, rotation that follows the IMU's true attitude | Done (patches 0024, 0027) |
| Battery ADC: `analogReadMilliVolts()` fails, "Calibration required eFuse bits not burnt" | To do: the eFuse's ADC calibration words, then a voltage set at run time on the pin's ADC channel |
| UDP under load: `endPacket(): could not send data: 12` at 100 rotation packets a second | To do: find what holds the transmit buffers (the Wi-Fi model's TX completion, or lwIP's pool) |
| The LED's color, not only that frames reached the RMT | To do: a WS2812 on the RMT's pin, decoded to a color the check reads |

## 3. ESP-SDR: the radio as a software-defined radio

The ESP32's receiver can hand its raw ADC samples to the CPU through a debug "dump
engine", and on the S3 the same engine can play samples out through the transmit DAC.
Three projects use it:

- [ESPARGOS/esp-sdr](https://github.com/ESPARGOS/esp-sdr) (ESP-IDF, GPL-3.0):
  receive only, on the whole family. On the S3: 80, 40 and 16 MS/s snapshots of up to
  16,380 samples, a continuous ring, spectra, and a line-based serial protocol (`INFO`,
  `FREQ`, `CAP16`, `CAP20` with CRC32) over UART0 or USB Serial/JTAG.
- [cyber-murmel/esp-sdr-zephyr](https://github.com/cyber-murmel/esp-sdr-zephyr) (Zephyr,
  GPL-3.0): its library adds transmit, I/Q playback through the DAC at 40 or 80 MS/s,
  and modems (GFSK, 802.15.4, OFDM), osmosdr and SoapySDR apps. Its
  [S3 register reference](https://github.com/cyber-murmel/esp-sdr-zephyr/blob/main/doc/dev/registers-esp32s3.md)
  is the specification this emulation follows.
- [ESPARGOS/SoapyESPSDR](https://github.com/ESPARGOS/SoapyESPSDR) (GPL-3.0): a SoapySDR
  driver for the ESP32-S31's streaming firmware over high-speed USB or Ethernet. Receive
  only.

Why it matters here: gbagate will use the S3's 2.4 GHz radio for backward compatibility
with the Game Boy Advance Wireless Adapter (AGB-015). Its plan
([gbagate `doc/RFU-RF.md`](https://github.com/HelaFaye/gbagate/blob/main/doc/RFU-RF.md))
starts from esp-sdr's S3 snapshots and esp-sdr-zephyr's DAC path:
- the AGB-015's MC13190 sends AM at 2.432 GHz, 60% deep, with Manchester data at
  4-6 Mbit/s;
- its host-tested baseband (`src/rfu_rf.c`) recovers the bits from CAP20 captures;
- `tools/rfu_capture.py` drives esp-sdr over serial.

The emulator can run that chain end to end, before anyone has an original adapter on a
bench.

### The registers (S3)

| Block | Register | What the model does |
|---|---|---|
| Dump engine | `DUMP_CTRL_REG` 0x60033d5c: run, trigger, done, circular, 16 or 40 MS/s, count | A one-shot capture writes `count` words at the selected rate (80, 40, 16 MS/s) into the owned bank, taking the virtual time it takes on the chip, then sets DONE; circular mode writes round a 16,384-word ring |
| | `DUMP_WRITE_INDEX_REG` 0x60033d60 | Words written; the live index in circular mode |
| | `DUMP_DAC_TRIG_REG` 0x60033d64: run, trigger (pulse: one pass; held: loop), done, 80 or 40 MS/s, count | Playback: the owned bank's words go to the air at the tuned frequency, if the front end is forced to transmit |
| | `DUMP_CONFIG_REG` 0x60033d90: four lane selectors | 0x000c2040 (sources 0-3) gives receive I/Q; any other selection gives zeros, logged |
| SRAM ownership | `SENSITIVE_INTERNAL_SRAM_USAGE_3_REG` 0x600c101c: bank n (0x3fcb0000 + n x 64 KiB) to the engine | While the engine owns a bank, CPU reads return garbage and writes are lost, as on the chip (a driver that forgets to hand a bank back fails here too). Bank 3 holds ROM data: handing it over is logged as a guest error |
| Front end | `FE_TX_CTRL_REG` 0x60006000: FE_FORCE_TXON | Without it the DAC sends nothing (on the chip: a spur near -6 MHz) |
| Tuning | Analog I2C block 0x63, regs 3-5 (the RF PLL's fractional-N divider), loaded by reg 0 = 0x17 | f = 0.75 x f_xtal x (32 + r3 + r4/2^8 + r5/2^16). The S3 has no analog-I2C master model yet: it decodes the master's transactions (base 0x6000e000) as the C5 and C6 models in this tree do |
| Gain | AGC force (0x6001c02c), the gain index in each word's bits 27:20 | The gain index scales the received signal and is written back in each word |

A captured word: I (10-bit signed, bits 9:0), Q (19:10), gain index (27:20).

### The air

`radio/airhub.py` already carries LoRa packets between QEMUs, with links and path loss.
An I/Q medium joins it:

- **Transmitters:** a QEMU's DAC playback, or a host-side scene: tones, noise, and
  scripted bursts (an AGB-015-style AM/Manchester burst at 2.432 GHz, a Wi-Fi beacon, a
  Bluetooth packet), each placed in virtual time.
- **Receivers:** each capture asks the hub for the samples at its tuned frequency, rate
  and window. The hub mixes what's on the air into that band: frequency offset,
  attenuation per link, the receiver's filter and gain, its noise floor, the ADC's 10
  bits and clipping.
- **Time:** samples are stamped in virtual time. Each QEMU stays within a window of the
  hub's clock, as the LoRa hub's time on air does. A capture is deterministic given the
  scene and a seed, so a test can say what bits it expects.

### Steps

| Step | What | Done when |
|---|---|---|
| SDR-0 | The S3 dump engine's receive side, the bank it writes, tuning by the analog I2C master, a built-in scene (`-global driver=esp32s3.sdr,property=scene,value=...`: tones, noise, AM bursts) | **Done** (patch 0028): `esp32s3-sdr-test`; ESPARGOS esp-sdr's S3 firmware (3ab3b65, ESP-IDF 5.5) answers `SYNC`, `INFO` and `LIMITS?`, tunes to 2437, 2432 and 1900 MHz exactly, and its `CAP20`/`CAP16` show each tone at its offset and level at 80, 40 and 16 MS/s. Its check goes with gbagate's RF work (SDR-1), per [testing your own firmware](testing.md#testing-your-own-firmware). Left for later: manual gain (esp-sdr checks the front end's readback of a forced gain, not modelled), the CPU's loss of an owned bank |
| SDR-1 | gbagate's RF-0/RF-1 chain on it | gbagate's `tools/rfu_capture.py` captures from the emulated esp-sdr over the emulated serial port, and `rfu_rf_analyze` recovers the bits of a synthetic AGB-015 burst at 80 and 40 MS/s, from 4 to 6 Mbit/s and 50 to 88% depth. This is gbagate's own synthetic test, run through the real firmware and the emulated radio instead of a pty |
| SDR-2 | The air hub's I/Q medium: two or more QEMUs, path loss, a host-side scene | Two esp-sdr QEMUs capture the same scripted burst; the closer one's is stronger by the link's loss |
| SDR-3 | Transmit: `DUMP_DAC_TRIG_REG`, FE_FORCE_TXON, playback into the air (one pass and looped) | One QEMU plays a waveform (esp-sdr-zephyr's TX, or a minimal ESP-IDF app with the same register sequence) and another captures it: the waveform comes back at the tuned offset, delayed by the air's latency only |
| SDR-4 | Continuous capture: circular mode, the write index, banks handed back and forth while it runs | esp-sdr's S3 continuous spectra and I/Q stream (USB Serial/JTAG), with the marker checks reporting no gaps at the emulated rate |
| SDR-5 | gbagate's RFU over RF, once gbagate has it: two gbagate nodes, and gbagate against an emulated AGB-015 (a host-side model of the adapter's waveform, built from gbagate's captures as RF-0 brings them) | Two gbagate QEMUs exchange RFU traffic over the emulated radio; then against the adapter model. Framing and TDMA follow gbagate's RF-3 findings |

Not planned yet:
- **SoapyESPSDR**: it needs the ESP32-S31, which Espressif's QEMU doesn't have. Its host
  tests could run against a stand-in streaming server; that tests the driver, not the
  emulation.
- **esp-sdr-zephyr's USB apps on the S3** (multiapp shell, osmosdr): they use the S3's
  USB OTG controller, which isn't emulated. Its serial apps (`osmosdr_serial`) are the
  way in.

### A shared 2.4 GHz air for Wi-Fi frames

gbagate's working Wireless Adapter path today is ESP-NOW between gbagate units. The Wi-Fi
model (patches 0019, 0020) gives one S3 an access point bridged to QEMU's network, but
two S3s can't hear each other's frames. The same air hub can carry 802.11 frames between
QEMUs: per channel, with the links' loss (a frame below sensitivity is lost), and
broadcast and action frames as ESP-NOW sends them. Then two gbagate nodes run their RFU
peer path over emulated ESP-NOW, and the SDR medium can show Wi-Fi traffic as a
receiver sees it.
