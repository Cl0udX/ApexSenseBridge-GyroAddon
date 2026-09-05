#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

./scripts/apply-patches.sh

echo "Configuring and building vendor/ApexSenseBridge (Release)..."
cmake -S vendor/ApexSenseBridge -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release
