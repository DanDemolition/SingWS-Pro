#!/bin/bash
# Build the SingWS Pro vocal-effects helper (arm64, macOS 15+).
#   ./build.sh          build the helper
#   ./build.sh --test   build and run the offline real-time core tests
set -euo pipefail
cd "$(dirname "$0")"

TARGET="arm64-apple-macos15.0"

if [[ "${1:-}" == "--test" ]]; then
    clang -O2 -Wall -Wextra -Werror -std=c11 vfx_dsp.c vfx_dsp_test.c -o vfx_dsp_test
    ./vfx_dsp_test
    # The callback contract is also checked structurally: the real-time core
    # must not reference any allocator, lock or I/O symbol.
    clang -c -O2 -std=c11 vfx_dsp.c -o vfx_dsp.o
    if nm -u vfx_dsp.o | grep -qE '_(malloc|calloc|realloc|free|pthread_mutex_lock|printf|write|open)$'; then
        echo "FAIL: real-time core references a forbidden symbol"; nm -u vfx_dsp.o; exit 1
    fi
    echo "  ok   real-time core references no allocator, lock or I/O symbol"
    rm -f vfx_dsp.o
    exit 0
fi

clang -c -O2 -Wall -Wextra -std=c11 -target "$TARGET" vfx_dsp.c    -o vfx_dsp.o
clang -c -O2 -Wall -Wextra -std=c11 -target "$TARGET" vfx_engine.c -o vfx_engine.o
swiftc -O -target "$TARGET" \
    -import-objc-header vfx_bridge.h \
    -framework CoreAudio -framework AudioToolbox -framework Foundation \
    SingWSVocalFX.swift vfx_dsp.o vfx_engine.o -o SingWSVocalFX
rm -f vfx_dsp.o vfx_engine.o
echo "built $(pwd)/SingWSVocalFX"
