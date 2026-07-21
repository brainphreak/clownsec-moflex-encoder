# Progress log — encoder efficiency work

Running log of verified results, build setup, and current state. Newest at top.

## 8×8 luma transform — IMPLEMENTED & VERIFIED (2026-07-21)

The biggest efficiency lever is done and measured. mobipeg only ever coded luma
with the 4×4 transform; MobiClip also supports an **8×8 luma transform** (the same
`mobi_add8x8_idct8` / `mobi_quant_8x8` math already used for chroma). Wiring it into
the inter luma encode path closes a large part of the efficiency gap.

**Patches** (in `patches/`, apply to `quatric/x264` + `quatric/mobipeg`):
- `x264-mobiclip-8x8-luma.patch` — adds the 8×8 inter-luma residual path in
  `encoder/macroblock.c` (forward `sub8x8_dct8` → `mobi_quant_8x8` with the luma
  `mobi_q8` table → store via `mobi_zigzag8x8` → reconstruct with `mobi_add8x8_idct8`),
  and lets `pps->b_transform_8x8_mode` be set for MobiClip so the (already-present)
  cavlc `use8x8` branch can fire. Intra stays I_4x4 (hard-forced in `analyse.c`).
- `mobipeg-mobiclip-8x8-luma.patch` — stops force-disabling 8×8 in `libavcodec/libx264.c`.
- Gated behind env `MOBI_8X8` so the default build still reproduces the 4×4 baseline
  byte-for-byte (clean A/B testing on one binary).

**Verified results** — single 400×240 eye, 72 frames, decoded with our bit-exact
decoder (`tdec`), PSNR vs the raw source:

| qyx | baseline 4×4 | 8×8 luma | bitrate saved | ΔPSNR |
|----|--------------|----------|---------------|-------|
| 2 | 558.6 kb/s @ 41.31 dB | 285.8 kb/s @ 41.17 dB | **48.8 %** | −0.14 dB |
| 3 | 271.8 kb/s @ 39.54 dB | 173.6 kb/s @ 39.42 dB | **36.1 %** | −0.12 dB |
| 4 | 153.4 kb/s @ 37.68 dB | 127.8 kb/s @ 37.65 dB | 16.7 % | −0.03 dB |
| 5 | 112.0 kb/s @ 35.63 dB | 106.9 kb/s @ 35.81 dB | 4.6 % | +0.18 dB |

The largest savings land at the **high-quality end (qyx2–3)** — exactly where good 3D
encodes live — for a negligible PSNR change. Correctness confirmed three ways: (1) both
streams decode all frames with **no desync**; (2) per-frame PSNR is **flat across the
whole GOP** (no accumulating drift — a wrong 8×8 IDCT would make PSNR fall monotonically
toward the last frame; it doesn't); (3) full 3D interleaved encode (`clip.mkv`, qyx2)
decodes to visually identical frames at **10.1 MB vs 13.2 MB (−24 %)**.

Next: let the RD mode decision *choose* 8×8 vs 4×4 per-MB (currently forced on for all
eligible inter MBs), which should recover the few cases where 4×4 wins (e.g. qyx5) and
push savings higher. Then sub-partitions, skip tuning, keyframe cap.

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
