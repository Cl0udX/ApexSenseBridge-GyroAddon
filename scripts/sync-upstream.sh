#!/usr/bin/env bash
# Moves vendor/ApexSenseBridge to the latest upstream main (or a given ref),
# then tries to re-apply our patches. If a patch fails, it stops so you can
# resolve the conflict by hand (edit the file, then regenerate the patch with
# `git -C vendor/ApexSenseBridge diff > patches/asb/000X-name.patch`).
set -euo pipefail
cd "$(dirname "$0")/.."

VENDOR_DIR="vendor/ApexSenseBridge"
REF="${1:-main}"

echo "Fetching upstream and checking out $REF ..."
git -C "$VENDOR_DIR" fetch origin
git -C "$VENDOR_DIR" checkout "$REF"
git -C "$VENDOR_DIR" pull origin "$REF" --ff-only || true

echo "New upstream commit:"
git -C "$VENDOR_DIR" log -1 --format="  %H %s"

echo
echo "Attempting to re-apply our patches..."
./scripts/apply-patches.sh

echo
echo "Success. Don't forget to commit the moved submodule pointer:"
echo "  git add vendor/ApexSenseBridge && git commit -m 'chore: sync upstream ApexSenseBridge to $REF'"
