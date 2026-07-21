# MobiClip encoder — efficiency analysis & roadmap

Goal: a MobiClip/moflex encoder that matches the official (Actimagine) encoder's
quality-per-bit, so 3D movies play smoothly on the 3DS (bitrate = decode load; a too-high
bitrate makes the player skip). Starting point: **mobipeg** (FFmpeg fork with a
libx264-based MobiClip encoder), which works but is **~3× less efficient** than official.

## Working baseline pipeline (both players)

`tools/moflex_encode3d.sh <input_sbs.mkv> <qyx> <out.moflex>` runs:

1. **`framepack=frameseq`** — interleave the full-SBS source's L/R eyes into one
   L,R,L,R stream (crop each eye, scale to 400×240). *Not* `stereo3d`, *not* SBS/hstack.
2. **mobipeg** — `-c:v mobiclip -mobi_qyx N -c:a pcm_s16le -mo_audio adpcm -ar 44100 -ac 2 -mo_layout 0`.
3. **`make3d.py`** — double `tb_num` (halve the declared fps) so the player's 2×-frame-rate
   3D detection fires.
4. **`patch_ts.py`** — share each L/R pair's per-frame `ef_c` timestamp (the official
   player pairs eyes by it; our player pairs by frame order and ignores it). Safe: `ef_c`
   is discarded by our decoder, decoded video is byte-identical.

### Gotchas (each one cost time to rediscover)
- **Audio**: feed `-c:a pcm_s16le -mo_audio adpcm` (the muxer re-encodes). Passing
  `-c:a adpcm_ima_moflex` double-encodes → ¾ of audio dropped.
- **Bitrate knob is `-mobi_qyx`** (0 = ~7 Mbps … 5 = ~1.4 Mbps … 8+ = ~72 kbps). `-b:v`,
  `-crf`, `-qp`, `-q:v` are all ignored; QP is pinned internally.
- **Layout 0 (interleave)**, not layout 4 (SBS) — SBS shows bars/interlaced rows.
- **`-mobiclip 2`** (MODS tables) for the codec mode.

### qyx bitrate (400×240 interleaved, sample clip)
| qyx | bitrate | note |
|----|---------|------|
| 2 | ~5.3 Mbps | best quality |
| 3 | ~3.2 Mbps | good; smooth on official/New-3DS, stutters on Old-3DS |
| 4 | ~2.1 Mbps | middle |
| 5 | ~1.4 Mbps | target bitrate, but pixelated |

## Why mobipeg is ~3× less efficient — verified against our bit-exact decoder

The gap is **not** a MobiClip format limit — it's that mobipeg's encoder leaves legal,
efficient tools unused. Confirmed by reading the decoder (the ground truth for what the
format supports):

| Tool | MobiClip supports it? | mobipeg uses it? | Evidence in the decoder |
|------|----------------------|------------------|-------------------------|
| **8×8 transform** | **Yes** | No (`b_transform_8x8=0`) | `idct8_pair`, `block8x8_coefficients_tab`, `pframe_block8x8_coefficients_tab` |
| **Sub-partitions** (16×8…4×4) | **Yes** | No (16×16 only) | `predict_motion_impl` recurses: split codes halve the block and re-read a motion VLC per half |
| **Skip blocks** | **Yes** | No (0%) | `index==0` = predicted MV, no delta; zero-residual coeff table = copy-from-ref |
| B-frames | **No** | — | decoder only references *past* frames (`sidx=current_pic−dist`); no forward refs |
| Large MVs | rejected out-of-bounds | capped at 32 | decoder returns `INVALIDDATA` if MC reads outside the frame |

So the three biggest levers — **8×8 transform, sub-partitions, skip** — are all valid and
unused. mobipeg disabled 8×8 only because *its* encoder-side 8×8 IDCT didn't match the
decoder; but we **have** the decoder's `idct8_pair` to match against.

## Roadmap — "our own encoder" = extend mobipeg's (github.com/quatric/x264)

The real encoder logic is in the x264 fork (`quatric/x264`), driven by `libavcodec/libx264.c`
in mobipeg. Plan, highest value first, each validated by encoding a clip and decoding it
with our bit-exact decoder (`pc_verify/`):

1. **Enable 8×8 transform** using the decoder's `idct8_pair` as the reference (biggest win).
2. **Enable sub-partitions** (let the encoder emit the split codes the decoder already reads).
3. **Enable skip blocks** (index-0 + zero residual for static regions).
4. **Raise `MOBICLIP_KEYINT_MAX`** (mobipeg's `encode.py` forces a keyframe every ≤90
   frames ≈ 2 s; the official caps at 30 s — fewer keyframes = more bits for P-frames).

B-frames and the MV-range cap are (mostly) genuine constraints and left alone.

## Encoder source state (github.com/quatric/x264, read 2026-07-21)

The fork already carries substantial MobiClip work — more than the shipped GUI binary
appears to use. Confirmed in `encoder/macroblock.c` / `encoder/analyse.c`:
- **MobiClip decoder-exact transforms already implemented**: `mobi_inverse4`,
  `mobi_idct8_1d`, `mobi_add8x8_idct8` (matches the decoder's `inverse4`/`idct`/
  `add_coefficients` bit-for-bit) — but currently wired **only for chroma 8×8**.
- **P_SKIP exists** (`b_mobi_skip`, macroblock.c ~1024) — gated by edge-row/ref conditions.
- MobiClip MC (half-pel averaging), MobiClip intra edge handling, `i_mobiclip` throughout.
- **Luma 8×8 is force-disabled**: `analyse.c` sets `h->mb.b_transform_8x8 = 0`
  (plus FFmpeg `libx264.c` sets `params.analyse.b_transform_8x8 = 0`).

**So the 8×8-transform fix is: wire the existing `mobi_add8x8_idct8` into the LUMA path**,
add the forward 8×8 transform + quant, let the mode decision pick 8×8 vs 4×4 (RD), and emit
the 8×8 luma block syntax the decoder already reads (`block8x8_coefficients_tab` /
`pframe_block8x8_coefficients_tab`). The hard, correctness-critical inverse math is done.

**Build note:** x264 + FFmpeg must be built from source to test any change (no `nasm`/`yasm`
on this machine → configure `--disable-asm`). Validate each change by encoding a clip and
decoding with `moflex_port/pc_verify/` (our bit-exact decoder).
