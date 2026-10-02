# Engineering log

What was tried, measured and changed, in order. For the current design, see
[how-it-works.md](how-it-works.md).

## 1. The first prototype was choppy

The first version uploaded a native-code loop that waited for each DMA half, copied it and wrote 80
bytes to USB. The Mac played the blocks through an AudioQueue into BlackHole. Measurements on the mic
(firmware 1.1.1) found three separate problems:

1. **27% of audio blocks were lost**, even with the mic idle.
2. **The Mac received audio in about 16 KB bursts every 130–160 ms.** The USB transfer only ended when macOS's buffer filled, which caused the lag.
3. **The Mac side had no real buffer.** It played whatever had arrived, filled gaps with zeros, and threw audio away whenever more than 100 ms piled up.

## 2. Why the mic lost blocks

- **USB was not the limit.** A tight write loop on the mic reaches about 320 KB/s; the audio needs 140 KB/s.
- **The loop starved USB.** The native loop never let MicroPython run its USB task, so writes blocked. A bytecode loop fixed the bursts (longest read gap down from 160 ms to about 10 ms), but still lost about 12% of blocks.
- **The effects engine steals the CPU.** A timing probe showed an interrupt taking about 260 µs of every 569 µs half, plus stalls of about 930 µs, longer than a whole half. No CPU copy loop can keep up with that.

## 3. Copying with DMA

Three spare DMA channels now copy every half in hardware, chained behind the factory channel, with a
hardware timestamp per block (see how-it-works). The first version had 16 slots, or 9 ms of slack.

- **Idle and with the 1 kHz test tone (firmware 1.1.1):** 0 lost blocks and 0 torn copies, stamps within 1.1 µs of the grid.
- **Test tone:** measured at 999.999 Hz. That confirms the 56,250 Hz rate, and the sample-to-sample jumps at block joins match those inside blocks.

## 4. The 3.5 mm plug changes everything

With the plug in, the factory firmware uses 68–74% of the CPU, up from about 46%. The mic's USB
ceiling drops to about 236 KB/s, and to about 180 KB/s while a sample plays.

- **The 16-slot reader fell behind** under that load: blocks were lost and copies torn.
- **12-bit compression made it worse.** One gain shift per block plus 12-bit samples needed 76 µs of CPU per block, more than the USB time it saved.
- **The fix was fewer Python calls.** One Python-level call costs 20–40 µs here. A single native call now drains up to 8 blocks into one packet, with 32 slots (18 ms of slack).
- **Result:** 0 lost blocks over 15,000 blocks with the plug in and the test tone playing. Over 30 s, three 18 ms gaps appeared during USB stalls of up to 158 ms; they are faded over.

## 5. The Mac side

- **Clocks:** measured over 20 s, the mic's clock matches the Mac's to within about 1 ppm. An apparent 0.07% pitch rise turned out to be the buffer slowly draining surplus audio from startup. That surplus is now dropped while the output is still silent.
- **Drift control:** gentler smoothing (2%, gain 0.1) brought pitch wobble down from about 1 cent to 0.14 cents, the same as the direct recording.

## 6. Talking to the mic while it streams

- **Mic state:** status packets carry effect, sample, handle, buttons and motion. `ui.sw()` does not report the three buttons, so presses come from a wrapper around the factory callback.
- **Commands:** the first attempt sent command bytes before Ctrl-C, and they vanished, because MicroPython's USB code clears stdin when Ctrl-C arrives. Sending Ctrl-C first, then the command in a separate write 10 ms later, works: every command applied with 0 blocks lost.
- **Button roles:** teenage engineering's hardware overview shows the middle button selects the sample and the bottom button plays it. The app follows that.

## 7. Speakers without howling

The speaker path is shifted up 5 Hz with a single-sideband shifter, so feedback cannot settle on one
frequency. A detector adds notch filters if a howl starts anyway, and a limiter caps the level.

- **Simulated room** (loop gain 1.5 at 1.3 kHz): 0.609 RMS without the guard, 0.0005 with it, and 0.0006 with notches alone.
- **False alarms:** none on noise or a held vowel.

## 8. A microphone called FX–USB

BlackHole v0.7.1 is rebuilt with a visible input-only device ("FX–USB") and a hidden output-only twin
that the app plays into. Installing it needs an admin password and restarts Core Audio. The app
reconnects when device IDs change.

## 9. Firmware

- **Versions:** teenage engineering's 1.1.1 and 1.1.2 have the same audio layout, and both work.
- **Images:** the official update files are unsigned RP2350 images (no signature block), so custom firmware would likely be accepted. FX–USB does not need it.
- **USB audio class:** the factory firmware has no `machine` module, so MicroPython's runtime USB device API is not available to add one.
