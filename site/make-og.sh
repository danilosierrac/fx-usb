#!/bin/sh
# Renders og-image.png (1200×630), the social sharing card, from the same drawing code as
# index.html. Needs Google Chrome.
set -eu
cd "$(dirname "$0")"
chrome="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
work="$(mktemp -d)"
python3 - "$work/og.html" <<'PY'
import sys, pathlib
page = pathlib.Path('index.html').read_text()
start = page.index("  const NS = 'http://www.w3.org/2000/svg';")
end = page.index("  /* Hero: the mic, talking")
drawing = page[start:end]
html = """<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=block">
<style>
html, body { margin: 0; width: 1200px; height: 630px; overflow: hidden; background: #EFF0EE; }
body { font-family: Inter, "Helvetica Neue", Arial, sans-serif; color: #1C1C1C; text-transform: uppercase; }
.name { position: absolute; left: 64px; top: 64px; display: flex; align-items: center; gap: 22px; font-size: 104px; line-height: .9; letter-spacing: -0.01em; }
.name svg { width: 66px; height: 66px; }
.name svg circle.dim { opacity: .15; }
.sub { position: absolute; left: 66px; top: 186px; font-size: 21px; line-height: 1.25; }
.rule { position: absolute; left: 64px; top: 254px; width: 560px; border-top: 2.5px solid #262626; }
.line { position: absolute; left: 64px; top: 286px; width: 560px; font-size: 52px; line-height: 1.04; }
.foot { position: absolute; left: 64px; bottom: 58px; display: flex; gap: 18px; align-items: center; }
.pill { border: 2.5px solid #1C1C1C; background: #EB5B2D; color: #fff; padding: 8px 16px; font-size: 22px; font-weight: 500; }
.url { font-size: 19px; color: #4F4F4C; text-transform: none; }
#art { position: absolute; left: 640px; top: 0; width: 560px; height: 630px; overflow: visible; }
</style></head><body>
<div class="name"><svg id="mark" viewBox="0 0 50 50"></svg>FX—USB</div>
<div class="sub">USB audio for the<br>EP—2350 FX mic</div>
<div class="rule"></div>
<div class="line">Your FX mic, now a microphone for your Mac.</div>
<div class="foot"><span class="pill">Free · for Mac</span><span class="url">danilosierrac.github.io/fx-usb</span></div>
<svg id="art" viewBox="0 0 560 630"></svg>
<svg style="display:none"><g id="menu-mark"></g></svg>
<script>(() => {
""" + drawing + """
  const art = document.getElementById('art');
  const m = mic(art, { x: 196, y: 64, s: 1.38, handle: 1, effect: 1, sample: 1, cable: 80, glow: true });
  m.holes.forEach((row, r) => row.forEach(h => h.setAttribute('opacity', r >= 4 ? 0.8 : 0)));
  const stamp = el('g', { transform: 'translate(8 388) scale(0.5)' }, art);
  stamp.innerHTML = `<path d="M200 24 L382 338 L18 338 Z" fill="#F8BC3F" stroke="#F8BC3F" stroke-width="40" stroke-linejoin="round"/>
    <g transform="translate(160 92) scale(0.8)" fill="#1C1C1C">
      <rect x="27" y="10" width="13" height="50" rx="6.5" transform="rotate(-14 33 56)"/>
      <rect x="44" y="2" width="13" height="56" rx="6.5" transform="rotate(-3 50 56)"/>
      <rect x="61" y="7" width="13" height="52" rx="6.5" transform="rotate(9 67 56)"/>
      <rect x="76" y="22" width="12" height="40" rx="6" transform="rotate(22 82 58)"/>
      <rect x="6" y="48" width="13" height="40" rx="6.5" transform="rotate(-48 12 70)"/>
      <path d="M24 50 H88 V70 C88 92 74 104 56 104 C38 104 24 92 24 74 Z"/>
      <rect x="40" y="92" width="34" height="20"/>
    </g>
    <text x="200" y="262" text-anchor="middle" font-family="Inter, Helvetica Neue, Arial" font-weight="600" font-size="42" fill="#1C1C1C">UNOFFICIAL!</text>`;
})();</script></body></html>"""
pathlib.Path(sys.argv[1]).write_text(html)
PY
"$chrome" --headless=new --disable-gpu --hide-scrollbars --window-size=1200,630 --virtual-time-budget=8000 \
  --screenshot="$PWD/og-image.png" "file://$work/og.html" 2>/dev/null
sips -g pixelWidth -g pixelHeight og-image.png | tail -2
