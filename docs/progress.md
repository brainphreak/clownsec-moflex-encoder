# Progress log — encoder efficiency work

Running log of verified results, build setup, and current state. Newest at top.

## Sub-partitions + RD, fine-QP, deadzone, aspect fix (2026-07-22)

Controlled harness: the Sheep source (`/Volumes/nasGOD/...Sbs.mp4`) encoded by BOTH the
official encoder (1200 kbps 2-pass moflex) and ours, compared on the same frames.

- **Sub-partitions ENABLED and bit-exact** (`MOBI_PSUB=1`): the fork's H_SPLIT/V_SPLIT
  writer round-trips fine (the old "doesn't round-trip" comment was stale). **Requires RD
  (`MOBI_SUBME=6`)** — without RD the split decisions are bad and quality *drops*.
  With RD: better PSNR at ~36 % lower bitrate on motion content, visibly sharper.
- **subme6**: only helps via sub-partitions/RD mode decisions; on its own it's
  content-dependent. The old "subme>=6 inflates files" comment is wrong with psub on.
- **Fine QP control works**: `-qp N` (12–39) gives 6 sub-steps per qyx level
  (`step = mobi_q8[qp%6] << qyx`), header written correctly. ~−10 % bitrate per +1 QP.
- **Quantizer rounding offset (`MOBI_DZ`, default 5)**: it's a round-up offset, not a true
  deadzone — LOWER = more aggressive. **DZ=3 is the efficiency sweet spot**: at equal
  bitrate ~+0.15 dB vs QP-stepping, and slightly lower temporal shimmer. DZ=2 is worse.
- **Keyframe interval**: `-g` works (wrapper honors gop_size; forced 90 by default).
  Raising to 480 saves only ~1 % now — scene-cut detection places most I-frames anyway.
  Still worth `-g 480`.
- **ASPECT BUG FIXED**: eyes were stretched to 400x240 regardless of source shape. An
  800x200 SBS source (2:1 per eye) must be letterboxed (400x200 + bars), as the official
  encoder does. `moflex_encode3d.sh` now uses
  `scale=400:240:force_original_aspect_ratio=decrease,pad=...` per eye.
- **Head-to-head vs official 1200 kbps, same frames** (after aspect fix): temporal
  stability (same-eye shimmer, reference-independent): official 4.74 vs ours 5.08 —
  within ~7 % (was 2.4x worse before this work). Visually the official is still somewhat
  cleaner at matched bitrate. PSNR across the two is NOT comparable (different
  scaler/preprocessing chains floor-limit the official's PSNR against our reference).
- **Remaining gap = rate control.** We are fixed-QP (CQP); the official is 2-pass and
  redistributes bits by scene complexity (our 2-min encode averaged 1677 kbps with quiet
  scenes overspent and busy scenes underspent). Two-pass/ABR is the next big lever.
- Dead ends confirmed: trellis (custom mobi quantizer ignores x264 trellis), AQ (CQP path
  overrides), multi-ref/same-eye referencing (ruled out against official's own ref mix).

**Current best config** (all env-gated, defaults unchanged):
`MOBI_8X8=1 MOBI_PSUB=1 MOBI_SUBME=6 MOBI_DZ=3` + `-mobi_qyx 2 -qp <21-24> -g 480`
with the letterboxing filter. `-qp` fine-tunes bitrate (~1200 kbps at qp 22-23 on
dialogue-heavy content).

## 8×8 INTRA luma + keyframe-flash fix — VERIFIED (2026-07-21)

Follow-up to the inter 8×8 work below. Enabling 8×8 only on P-frames left I-frames
on the 4×4 transform, so every keyframe showed a brief **grain/noise pulse** (the
4×4 I-frame keeps high-frequency detail the smoother 8×8 P-frames dropped). Confirmed
by per-frame high-frequency energy: 8×8-P-only had a sharp HF spike at every I-frame
(ratio 1.42× the neighbours, every ~90 interleaved frames); baseline 4×4 was flat.

**Fix: code I-frames 8×8 too.** Added a decoder-exact 8×8 **intra** luma path:
- `mobi_predict_8x8` / `mobi_pget8` (macroblock.h) — a faithful size-8 port of the
  proven `mobi_predict_4x4`, matching the decoder's `predict_intra(size=8)` /
  `intra_predict_fill` for all directional modes (0,1,3,4–8). Plane (mode 2) stays a
  16×16-MB predictor as in the decoder. Edge blocks forced to DC in
  `intra4x4_pred_mode` itself so encode and cavlc write the same mode.
- Intra 8×8 encode branch in `macroblock.c` (predict → `mobi_quant_8x8` intra →
  `mobi_add8x8_idct8`), then set `i_type=I_8x8` + `b_transform_8x8` so the existing
  cavlc I_8x8 branch emits the mask + 8×8 syntax. Same `MOBI_8X8` flag as inter (they
  MUST go together — 8×8-P beside 4×4-I is what causes the flash).

**Verified:**
- **Flash gone**: 8×8-P+I shows **0 I-frame HF spikes** (was 3, worst 1.42×).
- **Intra bit-exact**: I-frame-only clip decodes cleanly (a wrong 8×8 IDCT would
  desync the neighbour-dependent intra and drop PSNR to garbage; it stays ~40.8 dB).
- **Net win over the 4×4 baseline** (Micro Monsters, busy 3D nature footage, current
  binary): qyx2 6684→4975 kb/s (**−26 %**), qyx4 1934→1244 kb/s (**−36 %**). I-frames
  (the biggest frames) shrink 22–33 %.
- Full-pipeline 3D I-frame renders clean (centipede-on-litter, no artifacts).

**Build gotcha (cost real debugging time):** FFmpeg's `ffmpeg` target does **not**
depend on the external `x264-inst/lib/libx264.a`, so `make ffmpeg` will NOT relink
when only libx264 changed → you silently keep testing the old encoder. Always force a
relink after rebuilding x264: `rm -f ffmpeg ffmpeg_g && touch libavcodec/libx264.c &&
make -j ffmpeg`.

## 8×8 luma transform (inter) — IMPLEMENTED & VERIFIED (2026-07-21)

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

## Sub-partition glitch: 4x4-level splits caused flashing blocks — FIXED (2026-07-22)

User-reported: occasional flashing objects/garbage blocks in qyx0 encodes, clustered at
letterbox-boundary MB rows. Bisected on full-length encodes (short re-encodes do NOT
reproduce it — the glitch is state-dependent):

- skip-freeze off (MOBI_SKIP=0): unchanged → not skip
- short GOP (-g 90): unchanged → not long-GOP drift
- no sub-partitions: **zero glitches** → sub-partitions
- sub-partitions WITHOUT the 4x4 level (16x8/8x16/8x8 only): **zero glitches**, and
  bitrate essentially identical (6044 vs 6097 kb/s — the 4x4 level contributed ~nothing)

`MOBI_PSUB` is now a level: `1` = 16x8/8x16/8x8 (safe, recommended), `2` = adds
8x4/4x8/4x4 (known rare localized garbage — likely an analysis-vs-mobi-reconstruction
mismatch at the smallest partition sizes; not debugged further since the level is
near-worthless for bitrate anyway).

Recommended config unchanged except semantics: `MOBI_8X8=1 MOBI_PSUB=1 MOBI_SUBME=6 MOBI_DZ=3`.

## Rate control (ABR + 2-pass) — WORKING (2026-07-22)

The last structural gap vs the official encoder. Three pieces:

1. **Monotonic QP scale (`MOBI_RC=1`)**: the legacy QP scale WRAPS every 6 steps
   (quant shift fixed by -mobi_qyx, qp only picks the sub-step qp%6) — unusable for RC.
   New `mobi_shift(h, qp)` derives the shift from qp/6-2 exactly as the decoder derives
   it from the header QP, giving one monotonic scale qp 12..39. The frame-header write
   `(qp%6)+12+6*shift` then reduces to plain qp. Applied consistently at all quant,
   dequant, and header sites; verified strictly-monotonic bitrate over qp 18..30 and
   bit-exact decode.
2. **Per-frame RC must stay per-frame**: the mobi header carries only a per-frame QP
   (I absolute, P delta), so mb_tree / AQ / VBV row-adaptation would desync — forced off
   under MOBI_RC.
3. **Wrapper fix**: libx264.c unconditionally overrode rc to CQP 18 for mobiclip,
   clobbering the ABR that -b:v had set. Now only defaults to CQP when no bitrate given.

**Verified**: `-b:v 1500k` → 1459 (1-pass ABR) / 1498 (2-pass), `-b:v 3000k` → 2910;
per-frame QP varies (avg ~27 at 1500k); **bit-exact** with varying header QP deltas.
At matched average bitrate, 2-pass ≈ +0.35 dB mean over CQP; worst-scene p5 PSNR is
lower by design (complexity-weighted allocation, standard qcompress behavior).

**Usage** (2-pass, official-style):
```
MOBI_RC=1 MOBI_8X8=1 MOBI_PSUB=1 MOBI_SUBME=6 MOBI_DZ=3 \
  ffmpeg ... -c:v mobiclip -b:v 1200k -g 480 -pass 1 -passlogfile L -f null /dev/null
  ffmpeg ... -c:v mobiclip -b:v 1200k -g 480 -pass 2 -passlogfile L out.moflex
```
`-mobi_qyx` is ignored under MOBI_RC (QP carries the full scale).

Also noted: official-player-only "underwater edges" reported on v4 (not present in our
decoder's output or our player) — suspected official-decoder divergence on new syntax;
isolation files (NOsubparts / NO8x8) prepared for on-device A/B.
