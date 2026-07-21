# Encoder patches

Source patches that improve the MobiClip encoder's efficiency, on top of the
mobipeg toolchain (an FFmpeg fork + its x264 fork). Each patch is verified against
our bit-exact MobiClip decoder — see `../docs/progress.md` for measured results.

## 8×8 luma transform (`*-8x8-luma.patch`)

MobiClip supports an 8×8 luma transform, but mobipeg only ever coded luma as 4×4.
These patches wire the decoder-exact 8×8 transform (the same `mobi_add8x8_idct8` /
`mobi_quant_8x8` math mobipeg already uses for chroma) into the inter luma path.

**Result:** 36–49 % lower bitrate at qyx2–3 (the high-quality range) for a negligible
PSNR change; round-trips bit-exact through our decoder with no drift across the GOP.

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
