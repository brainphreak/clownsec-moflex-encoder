# Encoder work — current status & plan of action

*Snapshot 2026-07-23. The single place to resume from. Details in `progress.md` (newest-first log);
source changes are fully captured in `patches/` (apply to fresh `quatric/x264` + `quatric/mobipeg`
clones per the build recipe in `progress.md`).*

## Where we are

Goal: match the official Mobiclip encoder's quality at ~1200-2400 kbps so 3D movies are small
AND smooth on the 3DS. Started ~3x worse than official; now close but not equal.

### Working, verified improvements (all env-gated, in `patches/`)

| Feature | Flag | Effect |
|---|---|---|
| 8x8 luma transform (inter+intra) | `MOBI_8X8=1` | −26..49 % bitrate, keyframe-flash fixed |
| Sub-partitions 16x8/8x16/8x8 + RD | `MOBI_PSUB=1 MOBI_SUBME=6` | sharper motion, −36 % on motion content |
| Quantizer rounding offset | `MOBI_DZ=3` | +0.15 dB free, less shimmer |
| Fine QP scale | `-qp 12..39` | 6 sub-steps per qyx level (~10 %/step) |
| Keyframe interval | `-g 480` | small win; scene-cut handles the rest |
| Aspect-ratio letterboxing | (in `moflex_encode3d.sh`) | non-5:3 sources no longer stretched |
| Per-frame rate control (ABR + 2-pass) | `MOBI_RC=1` + `-b:v N -pass 1/2` | hits targets within 3 %, bit-exact |

**Current best command** (2-pass, official-style):
```
E="MOBI_RC=1 MOBI_8X8=1 MOBI_PSUB=1 MOBI_SUBME=6 MOBI_DZ=3"
env $E ffmpeg ... -c:v mobiclip -b:v 1700k -g 480 -pass 1 -passlogfile L -f null /dev/null
env $E ffmpeg ... -c:v mobiclip -b:v 1700k -g 480 -pass 2 -passlogfile L out.moflex
```
(plus the letterbox filter + make3d.py + patch_ts.py — see `tools/moflex_encode3d.sh`)

### Key findings (hard-won; do not re-derive)

1. **RD lambda vs mobi quantizer offset**: the mobi quantizer at header-QP N is ~one octave
   coarser than H.264 at N. x264's lambda must run ~6 QP below the header value or all mode
   decisions go stingy → noise at ANY bitrate (that was v5's "full of noise"). Fixed in
   `mobi_shift()`: RC header = qp+6 (validated ≡ the empirically-good legacy qyx2/qp22 pairing).
2. **4x4-level sub-partitions produce rare garbage blocks** (flashing objects) — `MOBI_PSUB=2`
   keeps them for debugging; never ship with it.
3. **Mobi header QP is per-frame only** → mb_tree/AQ/VBV must stay off under RC.
4. **The wrapper force-set CQP 18** for mobiclip, silently killing `-b:v` (fixed).
5. **Legacy QP scale wraps every 6 steps** (qp 24 coarser than 23) — RC needs `MOBI_RC=1`.
6. **Always compare on the SAME time window** — 30 s slices vary 2x in bitrate vs 2-min avg;
   two false alarms came from cross-window comparisons.
7. **Always force-relink ffmpeg after rebuilding x264** (`rm -f ffmpeg ffmpeg_g && touch
   libavcodec/libx264.c && make ffmpeg`) — several bogus "no effect" results came from stale binaries.
8. **PSNR across different preprocessing chains is invalid** — official-vs-ours quality must be
   judged by shimmer metrics and eyes, not PSNR-vs-our-reference.
9. Dead ends (measured, don't retry): x264 trellis (custom quant ignores it), AQ (per-frame QP
   only), multi-ref/same-eye referencing (official uses mostly dist-1 too), denoise for the
   grain issue, keyint beyond 480.

## Open items (the actual plan of action)

1. **PENDING USER VERDICT: v6 files** (`sheep_v6_1700k.moflex`, `sheep_v6_2400k.moflex` in
   ~/Downloads, built with the lambda fix). If still noisy → next lever is `-qcomp` (0.6
   default → try 0.75/0.85; higher = busy scenes starved less). Also finish the interrupted
   v5-vs-v6 PSNR A/B (decode both, mean + p5 vs `src120.yuv` — v5 files may have been moved
   to the Citra sdmc folder).
2. **PENDING USER TEST: official-player "underwater edges"** — isolation files delivered
   (`sheep_iso_NOsubparts.moflex`, `sheep_iso_NO8x8.moflex`). Play both in the OFFICIAL
   player: whichever is clean identifies which feature the official decoder handles
   differently from ours/FFmpeg. Then fix or gate that feature. (Effect absent in our decoder
   and reportedly absent/decent in the clownsec player.)
3. **qcompress + ip_factor tuning** against the official at 1200 kbps (its allocation still
   looks cleaner on faces at matched bitrate). Tools: the controlled harness (same-window
   shimmer + block-anomaly scans, `src120.yuv` reference, offA.yuv official decode).
4. **Consider making the winning env-flag set the DEFAULTS** in the patches once the official
   player artifact question is resolved, then update `moflex_encode3d.sh` to use
   `MOBI_RC` 2-pass by default with a `<bitrate>` argument instead of qyx.
5. Longer-term: chroma-quality look (chroma tables/DZ untouched), intra 8x8 edge modes
   (currently DC-forced at picture edges), and the encoder-side "underwater" if it turns out
   OUR path also shows it subtly.

## Test-harness inventory (scratchpad — regenerate if lost)

Session scratchpad may not survive; everything below is reproducible:
- `src120.yuv`: letterboxed interleaved source ref, Sheep 55–175 s (regenerate with the
  letterbox filter chain in `moflex_encode3d.sh` at `-ss 55 -t 120`).
- `off_all.yuv` / `offA.yuv`: official 1200 kbps decode (frames 0–3600 / aligned 2640+);
  official file lives in the Citra sdmc folder. Frame 2640 of the official = source 55.0 s.
- Glitch scanner + shimmer + edge-wobble metrics: python snippets in `progress.md` history
  (block-anomaly: 16x16 blockMSE > 1500 and > 30x frame median).
- Build tree: fresh clones + `patches/*.patch` + build recipe in `progress.md`.
- Decoder ground truth: `3ds-player/moflex_port/pc_verify/test_decode` (+ `test_eyedep`
  built with `-DMOBI_REFDIAG` for reference-distance histograms).

## Reference files currently in ~/Downloads (or moved to Citra sdmc)

sheep_v4_qyx0/1 (CQP, glitch-fixed), sheep_v5_1200k/1700k/2400k_2pass (OLD lambda — superseded),
sheep_v6_1700k/2400k (lambda fix — CURRENT), sheep_iso_NOsubparts/NO8x8 (official-player
isolation pair). Official reference: `...1200kbps_24.000fps_2pass.moflex` in Citra sdmc.
