#!/bin/sh
# Builds the FX–USB virtual microphone from BlackHole (GPL-3.0, github.com/ExistentialAudio/BlackHole).
# Device 1 "FX–USB": input only, visible, what call apps pick as their microphone.
# Device 2 "FX-USB-Bridge": output only, hidden; the FX–USB app plays into it by UID.
# Both share BlackHole's ring buffer, so what the app plays comes out of the microphone.
set -eu
cd "$(dirname "$0")"
tag=v0.7.1
[ -d src ] || git clone -q https://github.com/ExistentialAudio/BlackHole.git src
git -C src checkout -q "$tag"
bundle=local.fxusb.driver
cp ../dist/FX-USB.app/Contents/Resources/AppIcon.icns src/BlackHole/BlackHole.icns 2>/dev/null || true
rm -rf build
xcodebuild -quiet -project src/BlackHole.xcodeproj -configuration Release -target BlackHole \
  CONFIGURATION_BUILD_DIR="$PWD/build" PRODUCT_BUNDLE_IDENTIFIER=$bundle \
  CODE_SIGN_IDENTITY=- CODE_SIGNING_REQUIRED=NO DEVELOPMENT_TEAM= MACOSX_DEPLOYMENT_TARGET=13.0 \
  GCC_PREPROCESSOR_DEFINITIONS='$GCC_PREPROCESSOR_DEFINITIONS
  kDriver_Name=\"FX-USB\"
  kPlugIn_BundleID=\"'$bundle'\"
  kPlugIn_Icon=\"BlackHole.icns\"
  kHas_Driver_Name_Format=false
  kDevice_Name=\"FX–USB\"
  kDevice2_Name=\"FX-USB-Bridge\"
  kDevice_HasInput=true
  kDevice_HasOutput=false
  kDevice2_HasInput=false
  kDevice2_HasOutput=true
  kDevice2_IsHidden=true
  kManufacturer_Name=\"FX-USB\"
  kCanBeDefaultSystemDevice=false'
mv build/BlackHole.driver build/FX-USB.driver
codesign --force --deep --sign - build/FX-USB.driver
git -C src checkout -q -- BlackHole/BlackHole.icns
echo "built driver/build/FX-USB.driver"
