# Stress-testing a design before it's hardware

The aim: put a design's firmware and its hardware choices under realistic bad conditions
(weak and crowded radio, interference, brown-outs, glitches) before ordering a PCB,
buying parts or wiring a breadboard. Every condition is optional: a board runs clean by
default, and a test adds the stress it wants.

This is a design and a roadmap. What exists today is marked **(exists)**; the rest is
planned, in roughly the order below.

## What an emulator can and can't tell you

QEMU runs the firmware instruction by instruction against models of the chip and its
parts. It has no voltages, currents, fields or analog circuits. So every stress here is
modeled by its **effect on what the firmware sees**: a packet that arrives corrupted or
not at all, a bit flipped on a bus, a register that reads wrong, a reset, an instruction
that didn't execute. Those effects, and their rates and timing, come from datasheets,
published measurements, and (where you have them) your own; each model says where its
numbers come from.

This finds the firmware's weaknesses: what it does with a CRC error, a NACK, a
display that stops answering, a brown-out halfway through a flash write, a skipped
branch in a security check. It doesn't predict whether a particular layout passes an
EMC test; that needs field simulation or a lab. The KiCad integration below narrows the
gap by taking the stress's parameters from the actual board.

## 1. Radio environments

Radios in different QEMUs already share an air **(exists: `radio/airhub.py`)**, with per-
link RSSI, SNR, loss and corruption set by hand, and collisions decided in each radio
(capture by 6 dB) **(exists: patch 0006)**.

- **Positions and path loss (exists: `radio/environment.py`).** Nodes placed in 3D (the
  hub's JSON, `environment.nodes`), RSSI from transmit power, antenna gains and a
  log-distance path-loss model (free space at 1 m, an exponent for the terrain, log-normal
  shadowing). SNR against the thermal noise floor for the bandwidth plus the receiver's
  noise figure; packets below the demodulator's SNR limit for their spreading factor (SF7
  -7.5 dB to SF12 -20 dB, Semtech) are lost, and some just above it arrive corrupted.
- **Interferers (exists).** In-band transmitters with a position, power, frequency,
  bandwidth and duty cycle raise the noise at receivers near them.

Next:
- **Moving nodes.** Positions as functions of time, so a test can walk a node out of range
  and back, and watch a mesh reroute.
- **Interferers that are packets.** Today an interferer raises the noise; next, its
  transmissions are packets on the air, seen by CAD and colliding with real ones, so
  listen-before-talk and retries are exercised.
- **Bluetooth and Wi-Fi.** The same environment for vbt's virtual link (latency and loss
  from distance and interference) and, once Wi-Fi is emulated, for it.
- **Replays.** Record the air (every packet, its time, strength and fate) and replay it, so
  a failure seen once can be run again exactly.

## 2. Electromagnetic interference (EMI)

What noise does to a digital design shows up as errors on its wires. Planned, as
optional properties on the bus and pin models:
- **Bus errors.** Bit flips on SPI and I2C transfers at a rate (per bit, or in bursts),
  I2C NACKs and stuck-low SDA, UART framing and parity errors, SD CRC errors. Drivers'
  retry and recovery paths run.
- **Pin glitches.** Short pulses on inputs (a button bouncing, a long wire picking up
  noise): spurious edge interrupts, wrong reads.
- **Sources with timing.** Noise tied to events: a motor starting, the radio transmitting
  (the S3's own LoRa TX coupling into its SPI bus), a switching regulator's frequency.
- With a board from KiCad (5), the rates can follow the routing: a long, unshielded line
  or one running alongside the radio's feed gets more errors than a short, guarded one.

## 3. Voltage fault injection (VFI)

A supply that dips or glitches does three things to a chip, all modeled as effects:
- **Brown-out.** Below the brown-out detector's threshold the chip resets (the S3's
  RTC_CNTL brown-out interrupt and reset). A test schedules dips (by time, or at a code
  address: "during the flash write") and checks the firmware comes back consistent
  (its NVS, its files, its pairing data).
- **Glitches (exists: `faults/`).** A dip too short for a reset makes instructions
  misbehave: one skipped, a load returning the wrong value, a compare going the wrong
  way. Injected at an instruction address (and its N-th execution) through QEMU's GDB
  stub, so any board and firmware can be glitched unchanged: `faults/inject.py` (skip,
  flip or set a register after an instruction, flip or set memory, reset) and
  `faults/campaign.py`, which faults every instruction of the functions you name and
  sorts the outcomes (bypass, no effect, crash, hang). This is how secure-boot checks,
  PIN checks and length checks are attacked; the demo (`faults/target`, `faults/demo.py`,
  run in CI) shows a plain PIN check bypassed by 4 different single skips and a hardened
  one bypassed by none. Next: faults by time window and probability, and multi-fault
  (pairs of instructions).
- **Peripheral effects.** A dip that resets a part but not the chip (the display or radio
  on its own regulator): the part loses its state, and the firmware has to notice.

## 4. Electromagnetic fault injection (EMFI)

A pulse from a probe over the chip does locally what a voltage glitch does globally:
instruction and data faults, register and memory bit flips. Modeled the same way as
glitches (3), with spatial choice: which unit is hit (a CPU core, its cache or SRAM, a
peripheral's registers) instead of the whole chip, and a fault model per unit (bit
flips, stuck bits, a skipped instruction). Same injector (`faults/`), same campaigns;
today a memory fault can already be aimed at one SRAM region or peripheral register.

## 5. Boards from KiCad

Today a board is written by hand in `firmware/boards.py`. Planned, in two steps:
- **From the schematic.** `kicad-cli sch export netlist` gives every part and every net.
  A tool reads it, recognizes parts by their symbol and footprint (an ILI9341 module, an
  SX1262, an MCP23017, a button, a pull-up resistor), and writes the QEMU board: each
  part on its bus, each line linked to the ESP32 pin its net reaches, the pull-ups and
  open drains the board has. Parts it doesn't know are listed, not guessed.
- **From the routed board.** The PCB file adds what the schematic doesn't have: trace
  lengths, which nets run side by side and for how long, distances to the radio's
  antenna and feed, ground pour. These set the EMI model's rates per net (2) and the
  radio's antenna placement (1).
- **A KiCad plugin.** An action plugin in the PCB editor that exports the board, starts
  the emulation with a chosen firmware image and stress profile, and shows the results
  (serial output, screens, failures) without leaving KiCad.

## Order of work

1. Radio: positions, path loss, SNR limits and noise-raising interferers (done); moving
   nodes, interferers as packets, recording and replay.
2. Fault injector (done: instruction skip, register and memory faults, resets, campaigns,
   through the GDB stub); next, faults by time and probability, and multiple faults.
3. Brown-out (RTC_CNTL) and part power domains.
4. Bus and pin error models (SPI, I2C, UART, SD, GPIO).
5. KiCad netlist to board; then the PCB file's routing to EMI rates; then the plugin.
