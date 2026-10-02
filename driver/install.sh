#!/bin/sh
# Installs the FX–USB virtual microphone (needs an admin password) and restarts Core Audio,
# which briefly interrupts all sound on the Mac.
set -eu
cd "$(dirname "$0")"
sudo rm -rf /Library/Audio/Plug-Ins/HAL/FX-USB.driver
sudo cp -R build/FX-USB.driver /Library/Audio/Plug-Ins/HAL/
sudo killall -9 coreaudiod
echo "FX–USB installed"
