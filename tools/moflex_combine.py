#!/usr/bin/env python3
"""moflex_combine — losslessly join independently-encoded .moflex segments into one file.

Moflex is a chain of fixed-size blocks; periodic SYNC blocks (magic 0x4C32) carry an
8-byte big-endian microsecond timestamp. Combining = copy every block verbatim
(video + audio preserved bit-exact) and rewrite only the container bookkeeping so
each segment continues after the previous. No re-muxing, so audio is never truncated
(unlike ffmpeg -c copy on moflex, which drops most audio on 3D interleave).

The OFFICIAL 3DS player validates TWO fields that third-party demuxers (FFmpeg, ours)
ignore, and both must be fixed up when segments are joined:

1. Sync bytes [2:4]: a CHECKSUM of the timestamp — poly-0x0001 LFSR-CRC over the 8
   ts bytes, XOR 0xAAAA. A mismatch makes the official player hang at open.
   (Verified against 10031/10031 sync blocks of real official-encoder output.)

2. The per-block FLAGS byte (first byte of every non-sync block; after the stream
   descriptors in a sync block): its upper 6 bits are a rolling GROUP COUNTER —
   +4 (byte value) per sync group, mod 256, stamped identically on every block of
   the group (a continuity counter, like MPEG-TS's). A discontinuity makes the
   official player stop there — an appended segment restarts at its own first
   counter, so playback ended at the seam. Each appended segment's counter is
   shifted to continue from the previous segment's last group.

Usage:
  moflex_combine.py combine out.moflex seg1.moflex seg2.moflex [seg3 ...]
  moflex_combine.py split in.moflex <byte_offset> A.moflex B.moflex   # test helper
"""
import sys

def sync_check(ts_bytes):
    """bytes [2:4] of a sync block: LFSR(poly 0x0001) CRC over the 8 ts bytes, ^ 0xAAAA."""
    crc = 0
    for b in ts_bytes:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x0001) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc ^ 0xAAAA

def rvarb(d, i):
    """MSB-continuation varint used by the stream-descriptor list."""
    v = 0
    while True:
        b = d[i]; i += 1
        v = (v << 7) | (b & 0x7F)
        if not (b & 0x80):
            return v, i

def sync_flags_off(data, pos):
    """Offset of the flags byte inside a sync block at pos (skips the descriptor list)."""
    i = pos + 14
    while True:
        typ, i = rvarb(data, i)
        ssize, i = rvarb(data, i)
        i += ssize
        if typ == 0:
            return i

def walk_blocks(data):
    """Return ([[pos, ts], ...] for SYNC blocks, [(flags_byte_offset), ...] for ALL blocks);
    validates the block chain reaches EOF."""
    n = len(data); pos = 0; size = None; syncs = []; flag_offs = []
    while pos + 14 <= n:
        if data[pos] == 0x4C and data[pos + 1] == 0x32:          # sync block
            ts = int.from_bytes(data[pos + 4:pos + 12], 'big')
            size = int.from_bytes(data[pos + 12:pos + 14], 'big') + 1
            syncs.append([pos, ts])
            flag_offs.append(sync_flags_off(data, pos))
        else:                                                    # non-sync: flags byte first
            flag_offs.append(pos)
        if size is None:
            raise ValueError("first block is not a sync — not a moflex?")
        pos += size
    if pos != n:
        raise ValueError(f"block chain did not reach EOF ({pos} vs {n}) — corrupt/unsupported")
    return syncs, flag_offs

def combine(out, files):
    result = bytearray(); offset = 0; prev_ctr = None
    for fn in files:
        data = bytearray(open(fn, 'rb').read())
        syncs, flag_offs = walk_blocks(data)
        base = syncs[0][1]; maxnew = 0
        for s in syncs:                                          # rebase each sync ts
            newts = (s[1] - base) + offset
            tsb = newts.to_bytes(8, 'big')
            data[s[0] + 4:s[0] + 12] = tsb
            data[s[0] + 2:s[0] + 4] = sync_check(tsb).to_bytes(2, 'big')   # official player validates!
            maxnew = max(maxnew, newts)
        if prev_ctr is not None:                                 # rebase the group counter
            first_ctr = data[flag_offs[0]] & 0xFC
            shift = ((prev_ctr + 4) - first_ctr) & 0xFF          # continue +4 past prev segment
            for fo in flag_offs:
                b = data[fo]
                data[fo] = (((b & 0xFC) + shift) & 0xFF & 0xFC) | (b & 3)
        prev_ctr = data[flag_offs[-1]] & 0xFC
        result += data
        interval = (syncs[-1][1] - syncs[-2][1]) if len(syncs) >= 2 and syncs[-1][1] != syncs[-2][1] else 1001001
        offset = maxnew + interval                              # next segment starts here
        print(f"  + {fn}: {len(data)} B, {len(syncs)} syncs, {len(flag_offs)} blocks")
    open(out, 'wb').write(result)
    print(f"wrote {out}: {len(result)} B, total {offset/1e6:.2f}s")

def split(fn, at, a, b):
    d = open(fn, 'rb').read()
    open(a, 'wb').write(d[:at]); open(b, 'wb').write(d[at:])
    print(f"split @ {at}: A={at} B={len(d)-at}")

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("combine", "split"):
        print(__doc__); sys.exit(1)
    if sys.argv[1] == "combine": combine(sys.argv[2], sys.argv[3:])
    else: split(sys.argv[2], int(sys.argv[3]), sys.argv[4], sys.argv[5])
