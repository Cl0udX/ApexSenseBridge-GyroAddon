#!/usr/bin/env bash
# Applies our patches on top of the (clean) vendor/ApexSenseBridge submodule.
# Safe to re-run: it always resets the submodule to upstream HEAD first.
set -euo pipefail
cd "$(dirname "$0")/.."

VENDOR_DIR="vendor/ApexSenseBridge"
PATCH_DIR="patches/asb"

if [ ! -e "$VENDOR_DIR/.git" ]; then
    echo "Submodule not initialized. Run: git submodule update --init --recursive"
    exit 1
fi

echo "Resetting $VENDOR_DIR to clean upstream state..."
git -C "$VENDOR_DIR" reset --hard HEAD
git -C "$VENDOR_DIR" clean -fd

shopt -s nullglob
patches=("$PATCH_DIR"/*.patch)
if [ ${#patches[@]} -eq 0 ]; then
    echo "No patches found in $PATCH_DIR"
    exit 0
fi

for p in "${patches[@]}"; do
    echo "Applying $p ..."
    git -C "$VENDOR_DIR" apply --whitespace=nowarn "../../$p"
done

echo "All patches applied cleanly."
