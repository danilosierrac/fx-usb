#!/bin/sh
# Turns site/index.html (written as a page fragment: title, styles, body) into a complete HTML
# document for GitHub Pages, and copies the images and video next to it.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
out="${1:-_site}"
mkdir -p "$out"
url="https://danilosierrac.github.io/fx-usb/"
description="FX–USB is an unofficial Mac app that turns the teenage engineering EP–2350 FX MIC into a USB microphone, with its effects, lights and samples."
{
  printf '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
  printf '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
  printf '<meta name="description" content="%s">\n' "$description"
  printf '<meta property="og:title" content="FX–USB">\n<meta property="og:description" content="%s">\n' "$description"
  printf '<meta property="og:image" content="%sdemo-poster.jpg">\n<meta property="og:url" content="%s">\n' "$url" "$url"
  printf '<link rel="icon" href="app-icon.png">\n'
  sed -n '1,/<\/style>/p' "$here/index.html"
  printf '</head>\n<body>\n'
  sed '1,/<\/style>/d' "$here/index.html"
  printf '</body>\n</html>\n'
} > "$out/index.html"
cp "$here"/*.png "$here"/*.jpg "$here"/*.mp4 "$out/"
echo "built $out/index.html"
