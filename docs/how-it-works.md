# How FX–USB works

FX–USB has three parts: a reader that runs in the FX MIC's memory, the Mac app, and a virtual
microphone driver. This page describes the current design. For the measurements behind it, see
[engineering-log.md](engineering-log.md).

## The mic

The EP–2350 runs teenage engineering's MicroPython-based firmware on an RP2350. Over USB it offers a
serial console (`/dev/cu.usbmodemEP…`) and a drive, but no audio device.

Its audio engine works like this (firmware 1.1.1 and 1.1.2):

| Part | What it does |
|---|---|
| DMA channel 0 | Plays a 512-byte ring at `0x20008c00` into PIO0 (`0x50200010`): two halves of 32 stereo frames, 32-bit words, left channel in the even words. It retriggers itself every half, without an interrupt. |
| DMA channel 1 | Captures the microphone; its completion interrupt (IRQ 3) runs the effects. |
| Sample rate | Exactly 56,250 Hz. One half is 32 frames, or 568.89 µs. The µs timer runs from the same crystal. |
| CPU load | The effects take about 46% of the CPU, about 70% with the 3.5 mm plug in use, with regular stalls of about 1 ms. |

A loop on the CPU that copies each half before it is overwritten cannot keep up. Even a tight
native loop lost 27% of blocks.

## The reader on the mic (`Sources/fxmic_reader.py`)

The app uploads the reader at connect time, one top-level function per `exec`, with comments
stripped and indentation shrunk. The mic's Python heap is about 64 KB and fragmented.

**Hardware copy.** The reader claims three free DMA channels (C, D, E) with `rp2.DMA()` and chains
them behind channel 0:

```
DMA0 finishes a half ──chain──▶ C: writes the next slot address into D (and triggers it)
                                D: copies the 64 words DMA0 just started playing into that slot ──chain──▶
                                E: writes the µs timer into the slot's stamp
```

There are 32 slots of 256 bytes each, or 18 ms of slack; with less free memory it falls back to 16.
The slots are separate allocations, and C's address table makes them look like one ring. Only
DMA0's `CHAIN_TO` field is changed, and it is restored when the reader stops.

**Shipping blocks.** A plain bytecode loop calls one native (viper) function, `_fxu_drain`, which
copies every ready slot (up to 8) into one packet; the loop then writes the packet to USB. The loop
is deliberately bytecode: every pass lets MicroPython run its USB task and the factory button
callbacks. A loop that never yields starves USB, and writes then block.

**Status.** Every 50 ms the reader sends the effect, sample, buttons, handle position and
accelerometer. Button presses come from a wrapper around the factory `python_callback` that records
the press and always calls the original. The wrapper uses no globals, so it stays safe if the reader
is removed.

**Commands.** The Mac sends Ctrl-C, waits 10 ms, then sends the command. MicroPython clears stdin
when it receives Ctrl-C, so the command has to come after it. The reader catches the interrupt, reads
the command and carries on streaming. Any other byte (carriage return) stops it, and `finally`
unchains the DMA, clears and releases the channels, and restores the factory callback.

### Wire formats

All values are little-endian.

**Audio packet `FXP4`**: a 12-byte header, then 1 to 8 blocks of 66 bytes.

| Bytes | Field |
|---|---|
| 0–3 | `FXP4` |
| 4–7 | Hardware stamp of block 0 (µs, u32) |
| 8–9 | Sequence number of block 0 (u16, +1 per block) |
| 10 | Block count |
| 11 | 0 |
| per block | Stamp low 16 bits (u16), then 32 samples (int16, top 16 bits of the left channel) |

A slot that is overwritten while it is being copied is dropped, and the Mac sees the gap in the stamps.

**Status packet `FXS1`** (16 bytes): `FXS1`, effect (int8, -1 = clean, 0–3), sample (int8, 0–3),
buttons (bit 0 play/bottom, bit 1 sample select/middle, bit 2 effect/orange), handle (0–255),
flags (bit 0 effect change pending, bit 1 sample change pending), 0, then accelerometer x, y, z (int16).

**Commands**: `e0`–`e4` set the effect (clean, then 1–4), `s0`–`s3` set the sample, `p`/`q` press and
release play. Each one updates the mic's own LEDs.

## The Mac app (`Sources/`)

| Stage | What it does |
|---|---|
| USB reader | Opens the console with exclusive access, checks the model and firmware, uploads the reader, starts it, and polls the port. |
| Decoder | Splits `FXP4` and `FXS1` packets, independent of USB read boundaries. |
| Timeline | Turns hardware stamps into block positions (568.89 µs each). Up to 50 ms of missing blocks becomes a short fade of the last block; older or repeated blocks are dropped. It also removes DC, then applies gain and mute. |
| Call buffer | 40 ms target. A slow controller (2% smoothing, gain 0.1/s, limit ±0.5%) nudges the resampling ratio so the mic's clock and the output's clock never drift apart. Catmull-Rom resampling goes from 56,250 Hz to the output device's rate. It primes before playing, drops any surplus while still silent, and fades out if the mic stops. |
| Call output | AUHAL plays into the hidden "FX-USB-Bridge" device, and the Mac's default input is switched to "FX–USB" (restored on quit). It falls back to BlackHole 2ch. |
| Speakers | A second 30 ms buffer feeds any output except the bridge, through `FeedbackGuard`: a 5 Hz single-sideband frequency shift (255-tap Hilbert filter), a howl detector (2048-point FFT; a narrow peak at least 22 dB over its neighbours for 8 frames gets a notch, at most 8 notches) and a limiter. |
| Recording | An exact 75:64 resample (56,250 to 48,000 Hz), written as a mono 16-bit WAV. |
| UI | A SwiftUI window and menu bar panel from the same model, refreshed 30 times a second. The menu bar icon fills with the level. |

## The virtual microphone (`driver/`)

`driver/build.sh` builds [BlackHole](https://github.com/ExistentialAudio/BlackHole) v0.7.1 (GPL-3.0)
with these settings:

| Setting | Value |
|---|---|
| Driver | `FX-USB.driver`, bundle `local.fxusb.driver`, universal, ad-hoc signed |
| Device 1 | "FX–USB", UID `FX-USB_UID`, input only, visible: the microphone apps pick |
| Device 2 | "FX-USB-Bridge", UID `FX-USB_2_UID`, output only, hidden: the app plays into it |

The two devices share BlackHole's ring buffer, so what the app plays comes out of the microphone.
`driver/install.sh` copies the driver to `/Library/Audio/Plug-Ins/HAL` and restarts Core Audio.
xcodebuild splits preprocessor values on spaces and commas, so the device names avoid them.

## Measured results

| Test | Result |
|---|---|
| Reader, jack in, 1 kHz test tone playing, 15,000 blocks | 0 lost |
| Reader, jack in, test tone, 30 s | 99.8% delivered (three 18 ms gaps during 158 ms USB stalls, faded over) |
| Full chain into the call device, recorded back, 30 s | 0 dropouts, 0 glitch windows, pitch wobble 0.14 cents |
| Commands while streaming | All applied, 0 blocks lost |
| Feedback guard, simulated howling room | 0.609 RMS without the guard, 0.0005 with it; 0 false alarms on noise and a held vowel |
