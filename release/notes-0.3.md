**Unofficial.** FX–USB is a fan-made app. It is not made, checked or supported by teenage engineering.

FX–USB turns your EP–2350 FX MIC into a microphone for your Mac, with all its effects. Guide: https://danilosierrac.github.io/fx-usb/

## New in 0.3: buttons on your Mac

Give the mic's handle and buttons a job on your Mac (in the app: BUTTONS → SET UP):

- **Talk to an assistant.** Hold the handle and FX–USB holds your assistant's talk shortcut (ChatGPT, Claude, Siri, Dictation). The effects switch off while you talk (clean voice), then come back.
- **Record a voice note** while you hold a button. Notes are saved in Music/FX–USB/Voice notes.
- **Press a keyboard shortcut**, or **run a Shortcut** from the Shortcuts app.
- A button with a Mac job stops doing its mic job unless you tick "also on the mic".
- Pressing keys for other apps needs a one-time permission: System Settings → Privacy & Security → Accessibility → FX–USB.

Already have 0.2? Install this one over it.

## Install

1. Download **FX-USB.pkg** below.
2. Double-click it. macOS says it can't check it for malicious software, because the app isn't notarized by Apple yet. Click **Done**.
3. Open **System Settings → Privacy & Security**, scroll down, click **Open Anyway** and confirm with your password.
4. Follow the installer. It puts FX–USB in Applications and adds the **FX–USB** microphone. Core Audio restarts at the end, so sound drops for a second.
5. Lift the mic's lower lid, connect a USB-C cable that carries data, open FX–USB, and choose FX–USB as your microphone. Hold the handle to talk.

Or in Terminal, without the prompt:

```sh
curl -L -o /tmp/FX-USB.pkg https://github.com/danilosierrac/fx-usb/releases/latest/download/FX-USB.pkg && sudo installer -pkg /tmp/FX-USB.pkg -target /
```

## Needs

- A Mac with an Apple chip (M1 or newer), macOS 13 or later
- FX MIC firmware 1.1.1 or 1.1.2

## Files

- **FX-USB.pkg**: the app and the FX–USB microphone (recommended)
- **FX-USB.zip**: the app only. Without the microphone it uses BlackHole 2ch for calls, if installed.
- **SHA256SUMS.txt**: checksums

## Uninstall

```sh
sudo rm -rf /Applications/FX-USB.app /Library/Audio/Plug-Ins/HAL/FX-USB.driver && sudo killall -9 coreaudiod
```

## Licenses

The FX–USB microphone driver is [BlackHole v0.7.1](https://github.com/ExistentialAudio/BlackHole/tree/v0.7.1) by Existential Audio, licensed under GPL-3.0 (the license is inside the driver bundle). It is built unmodified, with the settings in [`driver/build.sh`](https://github.com/danilosierrac/fx-usb/blob/main/driver/build.sh).
