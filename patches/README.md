# Encoder patches

Source patches that improve the MobiClip encoder's efficiency, on top of the
mobipeg toolchain (an FFmpeg fork + its x264 fork). Each patch is verified against
our bit-exact MobiClip decoder — see `../docs/progress.md` for measured results.

## 8×8 luma transform (`*-8x8-luma.patch`)

MobiClip supports an 8×8 luma transform, but mobipeg only ever coded luma as 4×4.
These patches wire the decoder-exact 8×8 transform (the same `mobi_add8x8_idct8` /
`mobi_quant_8x8` math mobipeg already uses for chroma) into the inter luma path.

Covers **both** the inter (P-frame) and intra (I-frame) luma paths. They are enabled
together by design: 8×8 P-frames next to 4×4 I-frames make each keyframe a visible
grain/noise pulse, so I-frames must use 8×8 too.

**Result:** round-trips bit-exact through our decoder (no drift, no keyframe pulse).
On busy 3D nature footage, full 8×8 is **26 % smaller at qyx2 and 36 % at qyx4** than
the 4×4 baseline; on smoother content the high-quality range (qyx2–3) drops 36–49 %.
The largest frames (I-frames) shrink 22–33 %.

**Build note:** FFmpeg's `ffmpeg` target does not depend on the external `libx264.a`,
so after rebuilding x264 you must force a relink or you keep running the old encoder:
`cd mobipeg_src && rm -f ffmpeg ffmpeg_g && touch libavcodec/libx264.c && make -j ffmpeg`.

- `x264-mobiclip-8x8-luma.patch` → apply in the `quatric/x264` tree
- `mobipeg-mobiclip-8x8-luma.patch` → apply in the `quatric/mobipeg` tree

Gated behind the `MOBI_8X8` environment variable, so a default build reproduces the
4×4 baseline byte-for-byte (one binary does both — set `MOBI_8X8=1` to encode with 8×8).

### Build & use

Follow the build in `../docs/progress.md` (x264 → mobipeg ffmpeg), applying the patches
first:

```bash
cd x264        && git apply /path/to/x264-mobiclip-8x8-luma.patch
cd mobipeg_src && git apply /path/to/mobipeg-mobiclip-8x8-luma.patch
# ...configure + make as in progress.md...

# encode with the 8×8 transform enabled:
MOBI_8X8=1 ffmpeg -i in.mp4 -c:v mobiclip -mobi_qyx 2 ... out.moflex
```
