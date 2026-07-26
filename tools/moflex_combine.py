#!/usr/bin/env python3
"""moflex_combine — losslessly join independently-encoded .moflex segments into one file.

Moflex is a chain of 4KB blocks; periodic SYNC blocks (magic 0x4C32) carry an
8-byte big-endian microsecond timestamp. Combining = copy every block verbatim
(video + audio preserved bit-exact) and rewrite ONLY the sync timestamps so each
segment continues after the previous. No re-muxing, so audio is never truncated
(unlike ffmpeg -c copy on moflex, which drops most audio on 3D interleave).

Sync bytes [2:4] are a CHECKSUM of the timestamp: a poly-0x0001 LFSR-CRC over the
8 ts bytes, XOR 0xAAAA. The OFFICIAL player validates it on every sync (ours does
not) -- rewriting a timestamp without recomputing it makes the official player
hang on the first block. Verified against 10031/10031 sync blocks of real
official-encoder output.

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

def walk_syncs(data):
    """Return [[pos, ts], ...] for every SYNC block; validates the block chain reaches EOF."""
    n = len(data); pos = 0; size = None; syncs = []
    while pos + 14 <= n:
        if data[pos] == 0x4C and data[pos + 1] == 0x32:          # sync block
            ts = int.from_bytes(data[pos + 4:pos + 12], 'big')
            size = int.from_bytes(data[pos + 12:pos + 14], 'big') + 1
            syncs.append([pos, ts])
        if size is None:
            raise ValueError("first block is not a sync — not a moflex?")
        pos += size
    if pos != n:
        raise ValueError(f"block chain did not reach EOF ({pos} vs {n}) — corrupt/unsupported")
    return syncs

def combine(out, files):
    result = bytearray(); offset = 0
    for fn in files:
        data = bytearray(open(fn, 'rb').read())
        syncs = walk_syncs(data)
        base = syncs[0][1]; maxnew = 0
        for s in syncs:                                          # rebase each sync ts
            newts = (s[1] - base) + offset
            tsb = newts.to_bytes(8, 'big')
            data[s[0] + 4:s[0] + 12] = tsb
            data[s[0] + 2:s[0] + 4] = sync_check(tsb).to_bytes(2, 'big')   # official player validates!
            maxnew = max(maxnew, newts)
        result += data
        interval = (syncs[-1][1] - syncs[-2][1]) if len(syncs) >= 2 and syncs[-1][1] != syncs[-2][1] else 1001001
        offset = maxnew + interval                              # next segment starts here
        print(f"  + {fn}: {len(data)} B, {len(syncs)} syncs")
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
