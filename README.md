# moflex-encoder

Tools for producing **`.moflex`** (MobiClip 3D video) files for the Nintendo 3DS
*quickly*, by working around a performance bug in the official Mobiclip Multicore
Encoder.

## The problem

The official encoder compresses well, but its **final file-write stage is
single-threaded and O(n²)**: after the (fast, multi-core) encode passes finish at
99%, a lone thread inside `MobiclipEncoder.dll` grinds for **hours** assembling the
output — one core pegged, disk and the other 31 cores idle. A feature-length movie
can take **6+ hours** to write after a ~12-minute encode (observed: 3 h and still
going at 794 MB). It reproduces on every machine; it is not disk, RAM, antivirus, or
timer-resolution related, and the tool is closed-source so it can't be patched.

## The fix: split → encode in parallel → combine

Because the crawl scales with the *square* of length, cutting the movie into `k`
pieces makes each piece's finalize `(n/k)²` — and the pieces can encode **at the same
time** (the single-instance limit is only in the GUI shell; run copies via Sandboxie /
separate sessions / VMs). `k` parts ⇒ up to a **`k²` wall-clock speedup**
(6 hours → ~10 minutes for 6 parts), then the parts are joined back into **one file**.

```
source.mp4  ──split──▶  chunk_00.mp4 … chunk_k.mp4
                         │ (Nintendo GUI encodes each, in parallel)
                         ▼
                    seg_00.moflex … seg_k.moflex
                         │  moflex_combine.py
                         ▼
                     movie.moflex   (one file, Nintendo compression, full audio)
```

## Why not just `ffmpeg -c copy` concat?

mobipeg's ffmpeg (an FFmpeg fork with a moflex muxer) concatenates the **video**
perfectly, but its muxer **truncates the audio** on stream-copy of 3D content
(the 2× eye frame rate throws off its audio interleave). Verified with our own
bit-exact decoder: a 45 s clip came out with 2.9 s of audio. So the combine has to
happen at the container-block level instead — which is what `moflex_combine.py` does.

## tools/moflex_combine.py

Losslessly joins independently-encoded `.moflex` segments into one file.

Moflex is a chain of fixed-size blocks; periodic **sync blocks** (magic `0x4C32`)
carry an 8-byte big-endian microsecond timestamp. The combiner copies every block
**verbatim** (video *and* audio preserved bit-exact) and rewrites **only** the sync
timestamps so each segment continues after the previous. No re-muxing ⇒ audio is
never touched.

```
python3 tools/moflex_combine.py combine out.moflex seg1.moflex seg2.moflex [...]
```

**Validated** (against our own MobiClip decoder, which is bit-exact vs FFmpeg):

| Test | Result |
|---|---|
| split a file → recombine | audio **bit-identical**, video frame-count identical, only the sync timestamps change |
| combine two independent files | durations add correctly, audio + video fully preserved, clean block chain |
| mobipeg-encoded source (44.1 kHz, 4 KB blocks) | ✅ |
| Nintendo-encoded source (48 kHz, 2 KB blocks, 12 min) | ✅ audio bit-identical over 744 s |

## Status / roadmap

- [x] Understand the moflex container block format (see `docs/moflex-format.md`)
- [x] `moflex_combine.py` — lossless segment combiner (validated vs our decoder)
- [ ] **Source splitter** — cut the MP4 at keyframe- *and* eye-pair-aligned boundaries
      so each chunk encodes into a seam-safe 3D segment (each segment must start on a
      keyframe and on the **Left** eye, or 3D parity can flip at a join)
- [ ] End-to-end workflow script + on-device (3DS) validation of a combined file
- [ ] Parallel-encode harness (Sandboxie / multi-session driver for the GUI)

## Seam requirements (3D)

For a clean join, each segment must:
1. **start on a keyframe** — guaranteed when each chunk is encoded independently;
2. **start on the Left eye** — so split the source at **even frame counts** (pair
   boundaries). Odd splits can swap the eyes at a seam.

Audio/video stay aligned as long as the source is split on frame boundaries.
