#!/usr/bin/env python3
"""moflex_combine — losslessly join independently-encoded .moflex segments into one file.

Moflex is a chain of fixed-size blocks; periodic SYNC blocks (magic 0x4C32) carry an
8-byte big-endian microsecond timestamp. Combining = copy every block verbatim
(video + audio preserved bit-exact) and rewrite only the container bookkeeping so
each segment continues after the previous. No re-muxing, so audio is never truncated
(unlike ffmpeg -c copy on moflex, which drops most audio on 3D interleave).

The OFFICIAL 3DS player validates THREE structures that third-party demuxers
(FFmpeg, ours) ignore; all must be fixed up when segments are joined:

1. Sync bytes [2:4]: a CHECKSUM of the timestamp — poly-0x0001 LFSR-CRC over the 8
   ts bytes, XOR 0xAAAA. A mismatch makes the official player hang at open.

2. The per-block FLAGS byte (first byte of every non-sync block; after the stream
   descriptors in a sync block): its upper 6 bits are a rolling GROUP COUNTER —
   +4 (byte value) per sync group, mod 256, stamped identically on every block of
   the group. A discontinuity makes the official player stop there.

3. The SEEK INDEX: stream 1 ("data" stream) carries one frame at the head of the
   file — little-endian: u32 entry_count, u32 total_video_frames, u64 duration_us,
   then entry_count x 24-byte entries (u64 video_frame#, u64 timestamp_us,
   u64 sync_block_byte_offset). The official player takes the movie DURATION from
   this header and seeks via the entries, so a combined file inherits segment 1's
   duration and stops there. The index frame's byte size cannot change (it would
   shift every block), so the combiner rewrites it IN PLACE: segment 1's blob gets
   a merged index for the whole movie (downsampled evenly if the union has more
   entries than fit); later segments' embedded index blobs are rewritten with
   globally-rebased values so a mid-file refresh stays consistent.

Usage:
  moflex_combine.py combine out.moflex seg1.moflex seg2.moflex [seg3 ...]
  moflex_combine.py split in.moflex <byte_offset> A.moflex B.moflex   # test helper
"""
import struct
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

class BitReader:
    def __init__(self, d, byte):
        self.d = d; self.start = byte; self.bp = 0
    def pop(self):
        b = self.d[self.start + (self.bp >> 3)]
        bit = (b >> (7 - (self.bp & 7))) & 1
        self.bp += 1
        return bit
    def popn(self, n):
        v = 0
        for _ in range(n):
            v = (v << 1) | self.pop()
        return v
    def poplen(self):
        n = 1
        while self.pop() == 0:
            n += 1
        return n
    def after(self):
        return self.start + ((self.bp + 7) >> 3)

def walk(data):
    """One pass over the block chain. Returns (syncs [[pos, ts]...],
    flag_offs [flags byte offset of every block], idx_chunks [(payload_pos, size)...]
    of stream 1's first frame — the seek index). Validates the chain reaches EOF."""
    n = len(data); pos = 0; size = None
    syncs = []; flag_offs = []; idx_chunks = []; idx_done = False
    while pos + 2 <= n:
        if data[pos] == 0x4C and data[pos + 1] == 0x32:          # sync block
            ts = int.from_bytes(data[pos + 4:pos + 12], 'big')
            size = int.from_bytes(data[pos + 12:pos + 14], 'big') + 1
            syncs.append([pos, ts])
            i = pos + 14
            while True:                                          # skip descriptor list
                typ, i = rvarb(data, i)
                ssize, i = rvarb(data, i)
                i += ssize
                if typ == 0:
                    break
            flag_offs.append(i)
        else:                                                    # non-sync: flags byte first
            i = pos
            flag_offs.append(i)
        if size is None:
            raise ValueError("first block is not a sync — not a moflex?")
        flags = data[i]; ci = i + 1
        if flags & 2:
            ci += 2
        end = pos + size
        if not idx_done:                                         # chunk scan only until the
            while ci < end and data[ci] != 0:                    # index frame is assembled
                br = BitReader(data, ci)
                bits = br.poplen(); si = br.popn(bits); endframe = br.pop()
                if endframe:
                    b = br.poplen(); br.popn(b); br.pop()
                    b2 = br.poplen(); br.popn(b2 * 2 + 26)
                pkt = br.popn(13) + 1
                po = br.after()
                if si == 1:
                    idx_chunks.append((po, pkt))
                    if endframe:
                        idx_done = True
                ci = po + pkt
        pos += size
    if pos != n:
        raise ValueError(f"block chain did not reach EOF ({pos} vs {n}) — corrupt/unsupported")
    return syncs, flag_offs, idx_chunks

def idx_read(data, chunks):
    blob = b''.join(bytes(data[p:p + s]) for p, s in chunks)
    if len(blob) < 16:
        return None
    cnt, frames = struct.unpack('<II', blob[:8])
    dur, = struct.unpack('<Q', blob[8:16])
    if 16 + cnt * 24 > len(blob):
        return None
    entries = [struct.unpack('<QQQ', blob[16 + 24 * k:40 + 24 * k]) for k in range(cnt)]
    return {'cnt': cnt, 'frames': frames, 'dur': dur, 'entries': entries, 'cap': cnt,
            'blob_len': len(blob)}

def idx_write(data, chunks, cnt, frames, dur, entries, blob_len):
    """Scatter a rebuilt index blob back into the exact chunk byte ranges (same size)."""
    blob = struct.pack('<IIQ', cnt, frames, dur)
    for e in entries:
        blob += struct.pack('<QQQ', *e)
    blob += b'\0' * (blob_len - len(blob))                       # slack if fewer entries
    o = 0
    for p, s in chunks:
        data[p:p + s] = blob[o:o + s]
        o += s

def downsample(entries, cap):
    if len(entries) <= cap:
        return entries
    return [entries[round(k * (len(entries) - 1) / (cap - 1))] for k in range(cap)]

def combine(out, files):
    segs = []; offset = 0; prev_ctr = None; cum_bytes = 0; cum_frames = 0
    total_frames = 0; total_dur = 0
    for fn in files:
        data = bytearray(open(fn, 'rb').read())
        syncs, flag_offs, idx_chunks = walk(data)
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

        idx = idx_read(data, idx_chunks) if idx_chunks else None
        rebased = []
        if idx:
            rebased = [(f + cum_frames, t + offset, o + cum_bytes) for f, t, o in idx['entries']]
            total_frames += idx['frames']
            total_dur = offset + idx['dur']
        segs.append({'data': data, 'chunks': idx_chunks, 'idx': idx, 'rebased': rebased})

        interval = (syncs[-1][1] - syncs[-2][1]) if len(syncs) >= 2 and syncs[-1][1] != syncs[-2][1] else 1001001
        offset = maxnew + interval                              # next segment starts here
        cum_bytes += len(data)
        cum_frames = total_frames
        print(f"  + {fn}: {len(data)} B, {len(syncs)} syncs, {len(flag_offs)} blocks"
              + (f", index {idx['cnt']} entries dur={idx['dur']/1e6:.2f}s" if idx else ", no index"))

    if segs[0]['idx']:
        all_entries = [e for s in segs for e in s['rebased']]
        for k, s in enumerate(segs):                             # rewrite every embedded index
            if not s['idx']:
                continue
            ent = downsample(all_entries, s['idx']['cap']) if k == 0 else s['rebased']
            idx_write(s['data'], s['chunks'], len(ent), total_frames, total_dur,
                      ent, s['idx']['blob_len'])
        print(f"  index merged: {len(all_entries)} seek points -> {min(len(all_entries), segs[0]['idx']['cap'])} "
              f"in head, total {total_frames} frames, {total_dur/1e6:.2f}s")

    result = bytearray()
    for s in segs:
        result += s['data']
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
