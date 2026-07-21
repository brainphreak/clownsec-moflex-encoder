# Progress log — encoder efficiency work

Running log of verified results, build setup, and current state. Newest at top.

## Verified results (2026-07-21)

- **Working 3D pipeline plays on BOTH players.** qyx5 (~1.4 Mbps) confirmed on-device
  ("worked fine, just super pixelated"). qyx3 (~3.4 Mbps) and qyx4 (~2.2 Mbps) also confirmed
  playing ("both work, just pixelated"). Pixelation = mobipeg's ~3× inefficiency, not a bug.
- **Pipeline validated end-to-end**: `framepack=frameseq` → mobipeg `-mobi_qyx N` → `make3d.py`
  → `patch_ts.py`. Audio full (pcm→adpcm), tb correct (2002/48000), layout 0 interleave.
  `patch_ts` proven safe (decoded video byte-identical; only the discarded `ef_c` changes).
- **Toolchain builds from source and reproduces the shipped binary byte-for-byte.**
  Baseline encode (B.O.B. clip, qyx4, framepack): `frame I:4 / P:285`, `mb P I16..4: … P16..4:
  53.3% / 0 / 0 / 0`, `skip: 0.0%`, `1627.50 kb/s`, 1.18 MB — identical between the shipped
  `mobipeg-gui.app` ffmpeg and our from-source build. So modify→rebuild→test is trustworthy.
- **Quick-win levers ruled out (measured, not assumed):**
  - `-refs`, `-crf`, `-qp`, `-b:v`, `-x264opts` (scenecut/partitions/keyint): **all ignored**
    by the mobiclip encoder — every value gives identical output. Improvements require source.
  - **Skip works as designed**: `b_mobi_skip` fires only on near-static MBs (SAD ≤ 512); 0% on
    the busy test clip is correct behavior, not a bug.
  - Default reference count is already **3** (`x264 base.c:384`) — enough to reach the same-eye
    frame 2-back; refs is not the interleaved-3D bottleneck.
- **46.7% of P-macroblocks coded intra** on the busy clip — partly genuine (hard-to-predict
  motion/confetti), partly the missing tools (no sub-partitions to fit motion, 4×4-only transform).

## Build setup (reproducible, this machine)

No `nasm`/`yasm` → build with `--disable-asm`.

```bash
# x264 fork (the MobiClip encoder)
git clone --depth 1 https://github.com/quatric/x264.git x264
cd x264
./configure --prefix="$PWD/../x264-inst" --enable-static --disable-asm --disable-cli --bit-depth=8
make -j && make install                       # -> x264-inst/lib/libx264.a (has i_mobiclip/i_mobi_qyx)

# mobipeg FFmpeg linked against it
git clone --depth 1 https://github.com/quatric/mobipeg.git mobipeg_src
cd mobipeg_src
PKG_CONFIG_PATH="$PWD/../x264-inst/lib/pkgconfig" ./configure \
  --prefix="$PWD/../ffmpeg-inst" --enable-gpl --enable-libx264 \
  --disable-asm --disable-doc --disable-network --disable-ffplay --disable-ffprobe \
  --extra-cflags="-I$PWD/../x264-inst/include" --extra-ldflags="-L$PWD/../x264-inst/lib"
make -j ffmpeg                                 # -> mobipeg_src/ffmpeg
```

Validate any encoder change: encode a clip → decode with `3ds-player/moflex_port/pc_verify`
(our bit-exact decoder; `test_decode` for video, `test_audio` for audio) → confirm the
reconstruction matches, then measure kb/s at the same qyx.

## Current state / next

- Foundation: **done** (builds, reproduces baseline).
- **In progress: enable 8×8 luma transform** (biggest fixable win). See encoder-analysis.md.
  Decoder syntax (`process_block`): `tmp==0` → one 8×8 transform; `tmp>0` → 4×4 split (what
  mobipeg always writes). The inverse 8×8 IDCT (`mobi_add8x8_idct8`) already exists in the fork
  (chroma-only). Work: forward 8×8 transform + quant matching it, wire into luma reconstruction,
  RD mode decision 8×8 vs 4×4, and emit the MobiClip 8×8 coefficient bitstream.
- Then: sub-partitions, un-gate/tune skip, keyframe cap.
