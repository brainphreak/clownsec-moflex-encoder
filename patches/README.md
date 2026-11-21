# The moflex encoder Big Top ships

Big Top streams MP4/MKV to an Old 3DS by encoding them to moflex as they play. The encoder is
**Mobipeg-MOFLEX by Movie Bot** (https://github.com/Oldhimaster1/Mobipeg-MOFLEX), an FFmpeg +
x264 build with a MobiClip encoder and moflex muxer, (base commit 1ed42b1) with the changes in
`mobipeg-moflex-clownsec.patch` applied on top. FFmpeg and x264 are GPL; so is this encoder.

## What the patch adds

All of it is behind environment switches; with none set, output is byte-identical to Mobipeg-MOFLEX.

- `MOBI_8X8=1`, `MOBI_8X8_MS=1` — an 8x8 luma transform that matches the decoder exactly, with a per-block
  8x8 intra direction search
- `MOBI_CSPLIT=1` — a P-frame chroma 8x8 can be coded as four 4x4 transforms with a coded-quarter mask,
  chosen per block by distortion + bits (stops colour ringing around sharp edges)
- `MOBI_CSPLIT_I=1` — the same for intra chroma, with per-quarter decoder-exact prediction (experimental)
- `MOBI_INTER4_DZ=1` — fix: motion-compensated 4x4 luma blocks were quantized with intra rounding, so the
  inter deadzone (`MOBI_DZ`) never reached them
- `MOBI_P_INTRA_BIAS=n` — intra must beat inter by n% in a P frame
- `MOBI_AQ=1`, `MOBI_AQ_OFFSET`, `MOBI_AQ_DARK`, `MOBI_AQ_DARKQ` — a quantizer per frame from its
  brightness and detail, fitted to the official encoder's own choices (finest step on dark frames)
- `MOBI_KEY_DQ=n`, `MOBI_LRD=1`, `MOBI_LAMBDA_FULLQ=1` — experiments (see the notes in the code)
- the x264 reconstruction dump (`dump-yuv`) writes MobiClip's planar chroma correctly, so encoder output
  can be checked against a decoder byte for byte
- `MOBI_DUAL_EYES=1` (3D moflex, `-mo_layout 0` interleaved input) — each eye gets its own encoder,
  run side by side on two threads, and references only its own previous picture (two back in the
  decoder's list, `MOBI_REF_STRIDE=2`, set automatically). Each P frame's quantizer change is
  rewritten against the other eye's, and keyframes come at fixed spacing in both eyes (no scene-cut
  keyframes), so a seek lands on a keyframe pair. On 20 s of 1080p Big Buck Bunny 3D: 2.3x as fast,
  a third smaller, same quality; on 2 minutes, 20 MB instead of 47 MB, and the Old 3DS decodes it
  in 15.6 ms per pair instead of 30.1. Nintendo's own 3D files use same-eye references too (about
  44% of motion blocks in a retail sample), so it is standard MobiClip; the 3DS player's decoder
  and FFmpeg's decode it bit-identically
- `MOBI_REF_STRIDE=n` — the encoder sees every n-th frame of the stream: its "previous picture" is
  n back in the decoder's list
- the `MOBI_*` switches are read once (a cached `getenv`): the encoder looked them up inside its
  per-macroblock loops, and Windows' C runtime locks and walks the whole environment on every call --
  the Windows build ran at half its speed (no change to the output)

Measured against the official encoder (Nintendo's) on dark film and checked on Old 3DS hardware; every
switch combination Big Top uses reconstructs exactly what the 3DS decoder does.

## Building

`tools/build_encoder_cross.sh <linux-x86_64|windows-x86_64|macos-arm64|macos-x86_64> <source dir>` builds it
with Zig from one machine: a source tree is Mobipeg-MOFLEX's `native/source` with the patch applied.
