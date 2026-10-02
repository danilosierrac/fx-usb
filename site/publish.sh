#!/bin/sh
# Builds the guide and publishes it to the gh-pages branch, which GitHub Pages serves at
# https://danilosierrac.github.io/fx-usb/
set -eu
cd "$(dirname "$0")/.."
remote="$(git remote get-url origin)"
out="$(mktemp -d)"
sh site/build.sh "$out"
cd "$out"
git init -q -b gh-pages
git add .
git -c user.name="$(git -C "$OLDPWD" config user.name)" -c user.email="$(git -C "$OLDPWD" config user.email)" commit -q -m "Publish guide"
git push -q --force "$remote" gh-pages
echo "published to gh-pages"
