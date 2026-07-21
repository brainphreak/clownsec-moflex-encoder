# MOFLEX container — block layout (as needed for combining)

Reverse-engineered from the MobiClip decoder's demuxer (`moflex_demux.c`, a faithful
port of FFmpeg `libavformat/moflex.c`) and confirmed by walking real files.

A `.moflex` is a flat chain of **blocks**, all the same size within a file (2048 bytes
in Nintendo-encoded files, 4096 in mobipeg-encoded ones). Two kinds:

### Sync block — starts with magic `0x4C 0x32` ("L2")

```
offset  size  field
0       2     magic 0x4C32
2       2     (skipped by the demuxer)
4       8     timestamp, big-endian microseconds     ← the ONLY thing combine rewrites
12      2     block size − 1  (block length in bytes = value + 1)
14      …     stream-descriptor list, terminated by a type-0 entry
        1     flags byte
        …     chunk data (bit-packed headers + byte payloads), up to block end
```

Sync blocks appear roughly once per second of content. The timestamp updates **only**
here; between syncs the timestamp is implicitly the last sync's.

### Non-sync block — does **not** start with `0x4C32`

Just `flags byte` + chunk data. Reuses the size and timestamp of the last sync block.

### Walking the chain

Start at offset 0 (always a sync). At each block: if it begins with `0x4C32`, read a new
size from offset+12; otherwise keep the current size. Advance by `size`. A correct walk
lands exactly on EOF. (Naively scanning for `0x4C32` fails — that byte pair also occurs
inside payload data; you must follow the size chain.)

### Combining

1. Copy every block of every segment **verbatim** (this preserves the interleaved
   video + audio bitstream exactly).
2. In each segment after the first, rewrite each **sync block's** 8-byte timestamp to
   `(ts − segment_first_sync_ts) + running_offset`, then advance `running_offset` past
   the segment's duration.

Nothing else changes. Because payloads are untouched, video and audio are bit-exact;
only the coarse per-second sync timestamps are rebased so playback runs continuously.

### Streams (typical 3D file)

- `0` video — MobiClip, 400×240, frame-interleaved 3D (L,R,L,R… — 2 frames per pair).
- `1` data — empty timing stream.
- `2` audio — ADPCM-IMA-MOFLEX, stereo, 44.1 or 48 kHz.

3D is frame-interleaved: the video runs at ~2× the pair rate (an L and an R per
displayed instant), which is why FFmpeg reports "2× the duration" of the audio for the
same file — both are correct.
