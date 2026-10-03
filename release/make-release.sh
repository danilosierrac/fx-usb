#!/bin/sh
# Builds the downloadable installer (FX-USB.pkg) and a plain app zip into release/out.
# The installer puts FX-USB.app in /Applications and FX-USB.driver in /Library/Audio/Plug-Ins/HAL,
# then restarts Core Audio. Not notarized: there is no Developer ID certificate yet.
set -eu
cd "$(dirname "$0")/.."
version="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' Info.plist)"
sh build.sh
sh driver/build.sh
out=release/out
rm -rf "$out"; mkdir -p "$out/root/Applications" "$out/root/Library/Audio/Plug-Ins/HAL" "$out/scripts" "$out/resources"
ditto --noextattr --noqtn dist/FX-USB.app "$out/root/Applications/FX-USB.app"
ditto --noextattr --noqtn driver/build/FX-USB.driver "$out/root/Library/Audio/Plug-Ins/HAL/FX-USB.driver"
xattr -cr "$out/root"
cat > "$out/scripts/postinstall" <<'SH'
#!/bin/sh
# Restart Core Audio so the FX-USB microphone appears.
killall -9 coreaudiod 2>/dev/null || true
exit 0
SH
chmod +x "$out/scripts/postinstall"
# Never let Installer "relocate" the app to another copy it finds on the disk.
pkgbuild --analyze --root "$out/root" "$out/components.plist" >/dev/null
i=0
while /usr/libexec/PlistBuddy -c "Print :$i:RootRelativeBundlePath" "$out/components.plist" >/dev/null 2>&1; do
  plutil -replace "$i.BundleIsRelocatable" -bool NO "$out/components.plist"
  i=$((i + 1))
done
COPYFILE_DISABLE=1 pkgbuild --quiet --root "$out/root" --component-plist "$out/components.plist" --scripts "$out/scripts" \
  --identifier local.fxusb.installer --version "$version" --install-location / "$out/fxusb-component.pkg"
cp release/welcome.html "$out/resources/welcome.html"
cat > "$out/distribution.xml" <<XML
<?xml version="1.0" encoding="utf-8"?>
<installer-gui-script minSpecVersion="2">
  <title>FX–USB $version</title>
  <welcome file="welcome.html" mime-type="text/html"/>
  <options customize="never" require-scripts="false" hostArchitectures="arm64"/>
  <volume-check><allowed-os-versions><os-version min="13.0"/></allowed-os-versions></volume-check>
  <choices-outline><line choice="default"/></choices-outline>
  <choice id="default" title="FX–USB"><pkg-ref id="local.fxusb.installer"/></choice>
  <pkg-ref id="local.fxusb.installer" version="$version">fxusb-component.pkg</pkg-ref>
</installer-gui-script>
XML
COPYFILE_DISABLE=1 productbuild --quiet --distribution "$out/distribution.xml" --resources "$out/resources" --package-path "$out" "$out/FX-USB.pkg"
ditto -c -k --norsrc --noextattr --keepParent dist/FX-USB.app "$out/FX-USB.zip"
(cd "$out" && shasum -a 256 FX-USB.pkg FX-USB.zip > SHA256SUMS.txt)
echo "release/out: FX-USB.pkg, FX-USB.zip (version $version)"
