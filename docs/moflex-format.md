# MOFLEX container — block layout (as needed for combining)

Reverse-engineered from the MobiClip decoder's demuxer (`moflex_demux.c`, a faithful
port of FFmpeg `libavformat/moflex.c`) and confirmed by walking real files.

A `.moflex` is a flat chain of **blocks**, all the same size within a file (2048 bytes
in Nintendo-encoded files, 4096 in mobipeg-encoded ones). Two kinds:

### Sync block — starts with magic `0x4C 0x32` ("L2")

```
offset  size  field
0       2     magic 0x4C32
2       2     timestamp checksum, big-endian (see below)
4       8     timestamp, big-endian microseconds
12      2     block size − 1  (block length in bytes = value + 1)
14      …     stream-descriptor list, terminated by a type-0 entry
        1     flags byte
        …     chunk data (bit-packed headers + byte payloads), up to block end
```

Sync blocks appear roughly once per second of content. The timestamp updates **only**
here; between syncs the timestamp is implicitly the last sync's.

**Timestamp checksum (bytes 2–3).** A 16-bit check over the 8 timestamp bytes:

```python
def sync_check(ts_bytes):          # bytes [4:12] of the sync block
    crc = 0
    for b in ts_bytes:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x0001) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc ^ 0xAAAA
```

i.e. a CRC-16 with polynomial `0x0001` (a bare shift/feedback LFSR), init 0, XOR-out
`0xAAAA`. Verified against 10 031/10 031 sync blocks of Nintendo-encoder output.
FFmpeg's demuxer skips these bytes, but the **official 3DS player validates them and
hangs on the first mismatching block** — anything that rewrites a sync timestamp
(combining, retiming) must recompute this field, and any encoder must emit it.

### Non-sync block — does **not** start with `0x4C32`

Just `flags byte` + chunk data. Reuses the size and timestamp of the last sync block.

**Flags byte = group continuity counter.** Every block (sync blocks after their
descriptor list, non-sync blocks at offset 0) carries a flags byte whose upper 6 bits
are a rolling GROUP COUNTER: +4 (byte value) per sync group, mod 256, occasionally +8,
stamped identically on every block of the group. The low 2 bits are demuxer flags
(bit 0 = block-advance mode, bit 1 = two extra bytes follow) and are 0 in
Nintendo-encoder output. FFmpeg ignores the counter; the **official player checks
continuity and stops playback at the first discontinuity** — so naively concatenated
segments end at the seam. Joining segments requires shifting each appended segment's
counter to continue +4 from the previous segment's last group (internal +4/+8 deltas
are preserved by a constant shift).

### Stream 1: the seek index (why the official player knows the duration)

The "data" stream (descriptor type 4, stream index 1) is not empty: it carries ONE
frame, split into ~2 KB chunks filling the first blocks of the file. Little-endian:

```
u32   entry_count
u32   total_video_frames          (both eyes; = duration x 2 x fps for 3D)
u64   duration_us                 ← what the official player displays as movie length
then entry_count x 24-byte entries:
u64   video frame number
u64   timestamp_us                (0-based; sync ts − 1)
u64   byte offset of a sync block (a seek point, ~every 4-5 s)
```

The official player takes the movie duration from this header and seeks via the
entries; it will not play past what the index covers. FFmpeg ignores stream 1
entirely. When combining, the index frame's byte size cannot change (it would shift
every block behind it), so the merged index is rewritten IN PLACE: segment 1's blob
gets header totals for the whole movie plus the union of all segments' (rebased)
entries, downsampled evenly to the original entry count; later segments' embedded
blobs are rewritten with globally-rebased values.

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
