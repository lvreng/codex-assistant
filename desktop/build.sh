#!/usr/bin/env bash
set -euo pipefail
PROJECT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cmake -S "$PROJECT/tools/preview" -B "$PROJECT/build/desktop" -G Ninja -DPET_BUILD_DESKTOP=ON
cmake --build "$PROJECT/build/desktop" --parallel "${CMAKE_BUILD_PARALLEL_LEVEL:-8}"
