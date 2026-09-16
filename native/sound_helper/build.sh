#!/bin/bash
# Build the SingWS Pro sound helper (arm64, macOS 15+).
set -euo pipefail
cd "$(dirname "$0")"
swiftc -O -target arm64-apple-macos15.0 \
  -framework SoundAnalysis -framework AVFoundation -framework CoreAudio \
  SingWSSoundHelper.swift -o SingWSSoundHelper
echo "built $(pwd)/SingWSSoundHelper"
swiftc -O -target arm64-apple-macos15.0 -framework CoreAudio \
  SingWSMicMeter.swift -o SingWSMicMeter
echo "built $(pwd)/SingWSMicMeter"
