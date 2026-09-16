#!/bin/bash
# Build the experimental SoundAnalysis probe (arm64, macOS 15+). Not shipped.
set -euo pipefail
cd "$(dirname "$0")"
swiftc -O -target arm64-apple-macos15.0 \
  -framework SoundAnalysis -framework AVFoundation \
  SingWSSoundProbe.swift -o SingWSSoundProbe
echo "built $(pwd)/SingWSSoundProbe"
