#!/usr/bin/env bash
# Cross-build the moflex encoder (Mobipeg-MOFLEX by Movie Bot + the clownsec patch) with Zig, for
# the platforms Big Top ships on. Based on Mobipeg-MOFLEX's scripts/build-cross.sh, with:
#  - a Linux target (static musl: runs on any distribution, no glibc version tie)
#  - threads ON (live encoding must beat real time on modest machines): pthreads on Linux/macOS,
#    Win32 threads on Windows
#  - Zig's own archive tools (zig ar / zig ranlib), so no separate LLVM install is needed
# Usage: build_encoder_cross.sh <linux-x86_64|windows-x86_64|macos-arm64|macos-x86_64> <x264/ffmpeg source dir>
set -euo pipefail
TARGET="${1:?target}"; SRC="${2:?source dir with x264/ and ffmpeg/}"
ZIG="${ZIG:-$(command -v zig)}"
BASE="${BUILD_ROOT:-$PWD/.build-cross}/$TARGET"
PREFIX="$BASE/local"
mkdir -p "$BASE/x264" "$BASE/ffmpeg" "$PREFIX" "$BASE/tools"
case "$TARGET" in
  linux-x86_64)   TRIPLE=x86_64-linux-musl;   HOST=x86_64-linux-musl;    OS=linux;   ARCH=x86_64; X264T=--enable-thread;      FFT=(--enable-pthreads) ;;
  windows-x86_64) TRIPLE=x86_64-windows-gnu;  HOST=x86_64-w64-mingw32;   OS=mingw32; ARCH=x86_64; X264T=--enable-win32thread; FFT=(--enable-w32threads) ;;
  macos-arm64)    TRIPLE=aarch64-macos.11.0;  HOST=aarch64-apple-darwin; OS=darwin;  ARCH=aarch64; X264T=--enable-thread;     FFT=(--enable-pthreads) ;;
  macos-x86_64)   TRIPLE=x86_64-macos.11.0;   HOST=x86_64-apple-darwin;  OS=darwin;  ARCH=x86_64; X264T=--enable-thread;      FFT=(--enable-pthreads) ;;
  *) echo "unsupported target $TARGET"; exit 1 ;;
esac
# compiler and archive-tool wrappers (Zig), local to the build directory
# (drops the linker flags Zig rejects, as Mobipeg-MOFLEX's own wrapper does)
cat > "$BASE/tools/cc" <<EOF
#!/usr/bin/env python3
import os, sys
drop = ("-Wl,-dynamic,-search_paths_first", "-Wl,--pic-executable,-e,mainCRTStartup")
os.execv("$ZIG", ["$ZIG", "cc", "-target", "$TRIPLE"] + [a for a in sys.argv[1:] if a not in drop])
EOF
printf '#!/bin/sh\nexec "%s" ar "$@"\n' "$ZIG" > "$BASE/tools/ar"
printf '#!/bin/sh\nexec "%s" ranlib "$@"\n' "$ZIG" > "$BASE/tools/ranlib"
chmod +x "$BASE/tools/"*
export ZIG_GLOBAL_CACHE_DIR="${ZIG_GLOBAL_CACHE_DIR:-$BASE/zig-cache}"
rsync -a --delete "$SRC/x264/" "$BASE/x264/"
rsync -a --delete "$SRC/ffmpeg/" "$BASE/ffmpeg/"
cd "$BASE/x264"
CC="$BASE/tools/cc" AR="$BASE/tools/ar" RANLIB="$BASE/tools/ranlib" \
  ./configure --prefix="$PREFIX" --host="$HOST" --enable-static --disable-cli \
  --disable-opencl --disable-asm $X264T --enable-pic --extra-cflags='-O3'
make -j "${JOBS:-8}" >/dev/null
make install >/dev/null
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig"
cd "$BASE/ffmpeg"
./configure --prefix="$PREFIX" --enable-cross-compile --target-os="$OS" --arch="$ARCH" \
  --cc="$BASE/tools/cc" --ar="$BASE/tools/ar" --ranlib="$BASE/tools/ranlib" --nm=nm \
  --pkg-config=pkg-config --enable-gpl --enable-libx264 --disable-debug --disable-doc \
  --disable-ffplay --disable-ffprobe --disable-stripping "${FFT[@]}" --disable-autodetect --disable-devices \
  --disable-asm --disable-x86asm --disable-inline-asm --enable-static --disable-shared \
  --disable-videotoolbox --disable-audiotoolbox --disable-securetransport \
  --extra-cflags="-O3 -I$PREFIX/include" --extra-ldflags="-L$PREFIX/lib -s" \
  $( [ "$OS" = linux ] && echo --extra-ldflags=-static ) >/dev/null
make -j "${JOBS:-8}" >/dev/null
make install >/dev/null
ls -la "$PREFIX/bin/"
