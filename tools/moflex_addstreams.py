#!/usr/bin/env python3
"""moflex_addstreams — inject a second audio track and/or embedded subtitles into a .moflex.

The result stays a fully valid file for the OFFICIAL player (which plays the FIRST audio
stream and ignores the added ones) while our player exposes the extra track and subtitles:

  stream 0: video               (untouched)
  stream 1: seek index          (byte offsets patched -- blocks shift when streams are added)
  stream 2: primary audio       (untouched -- e.g. the English dub)
  stream 3: added audio         (--audio track.wav: 16-bit PCM mono/stereo, e.g. Japanese)
  stream 4: added subtitles     (--srt subs.srt: whole file as ONE data-stream frame at the
                                 head, same pattern the seek index uses)

Everything the official player validates is rebuilt: sync timestamp checksums, per-block
group continuity counters (each group keeps its ORIGINAL counter; groups just grow blocks),
and the seek index (same entry count, offsets remapped). Audio packets use the same framing
as native files: 4-byte per-channel IMA state headers, 256-sample subframes, 1024 samples
per packet, endframe timestamps relative to the enclosing sync group.

Usage:
  moflex_addstreams.py in.moflex out.moflex [options]

  --audio eng.wav      first audio stream (2) -- what the official player plays
  --audio2 jpn.wav     second audio stream (3) -- selectable in our player
  --srt subs.srt       embedded subtitles (stream 4)
  --strip-audio        DROP the file's existing audio first (we own both tracks;
                       the encode-pipeline audio is reference only)
  --audio-first        alternative to --strip-audio: keep the existing audio but move
                       it to stream 3 (bit patch); the --audio track takes stream 2
  --normalize          peak-normalize each provided WAV to -0.2 dBFS (loud, no clipping)
"""
import struct
import sys
import wave

# ---------------- container primitives (shared knowledge with moflex_combine) ----------------

def sync_check(ts_bytes):
    crc = 0
    for b in ts_bytes:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x0001) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc ^ 0xAAAA

def rvarb(d, i):
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

class BitWriter:
    def __init__(self):
        self.bits = []
    def put(self, v, n):
        for k in range(n - 1, -1, -1):
            self.bits.append((v >> k) & 1)
    def put_len(self, n):          # unary length as poplen reads it: (n-1) zeros then a 1
        self.bits.extend([0] * (n - 1)); self.bits.append(1)
    def bytes(self):
        out = bytearray((len(self.bits) + 7) // 8)
        for i, b in enumerate(self.bits):
            if b:
                out[i >> 3] |= 0x80 >> (i & 7)
        return bytes(out)

def chunk_header(si, endframe, efv, size, ef_bit=0):
    """Packet header preceding `size` payload bytes (payload starts at the next byte boundary)."""
    w = BitWriter()
    si_bits = max(1, si.bit_length())
    w.put_len(si_bits); w.put(si, si_bits)
    w.put(1 if endframe else 0, 1)
    if endframe:
        w.put_len(1); w.put(0, 1)        # X field = 0 (1 bit), as native packets carry
        w.put(ef_bit, 1)                 # audio uses 0; head data frames (index) use 1
        w.put_len(1)                     # b2 = 1 -> timestamp width 1*2+26 = 28 bits
        w.put(efv & ((1 << 28) - 1), 28)
    w.put(size - 1, 13)
    return w.bytes()

# ---------------- IMA-ADPCM encoder (mirror of decoder/adpcm_moflex.c) ----------------

STEP = [7,8,9,10,11,12,13,14,16,17,19,21,23,25,28,31,34,37,41,45,50,55,60,66,73,80,88,97,
        107,118,130,143,157,173,190,209,230,253,279,307,337,371,408,449,494,544,598,658,
        724,796,876,963,1060,1166,1282,1411,1552,1707,1878,2066,2272,2499,2749,3024,3327,
        3660,4026,4428,4871,5358,5894,6484,7132,7845,8630,9493,10442,11487,12635,13899,
        15289,16818,18500,20350,22385,24623,27086,29794,32767]
IDX = [-1,-1,-1,-1,2,4,6,8]

class ImaCh:
    __slots__ = ('pred', 'step')
    def __init__(self):
        self.pred = 0; self.step = 0

def ima_nibble(ch, sample):
    step = STEP[ch.step]
    diff = sample - ch.pred
    nib = 0
    if diff < 0:
        nib = 8; diff = -diff
    if diff >= step:
        nib |= 4; diff -= step
    if diff >= step >> 1:
        nib |= 2; diff -= step >> 1
    if diff >= step >> 2:
        nib |= 1
    # reconstruct exactly like the decoder
    d = ((2 * (nib & 7) + 1) * step) >> 3
    ch.pred += -d if (nib & 8) else d
    ch.pred = max(-32768, min(32767, ch.pred))
    ch.step += IDX[nib & 7]
    ch.step = max(0, min(88, ch.step))
    return nib

PKT_SAMPLES = 1024                     # per channel per packet (multiple of 256, like native)

def adpcm_packets(pcm, channels, rate):
    """[(start_us, payload_bytes)] for the whole track; encoder state carries across packets."""
    chs = [ImaCh() for _ in range(channels)]
    total = len(pcm) // channels
    out = []
    pos = 0
    while pos < total:
        n = min(PKT_SAMPLES, total - pos)
        n -= n % 256                                   # subframes are exactly 256 samples
        if n <= 0:
            break
        payload = bytearray()
        for c in range(channels):                      # 4-byte state header per channel
            payload += struct.pack('<hh', chs[c].step, chs[c].pred)
        # careful: the header stores the state BEFORE encoding this packet's samples
        for sf in range(n // 256):
            base = pos + sf * 256
            for c in range(channels):
                for k in range(0, 256, 2):
                    lo = ima_nibble(chs[c], pcm[(base + k) * channels + c])
                    hi = ima_nibble(chs[c], pcm[(base + k + 1) * channels + c])
                    payload.append(lo | (hi << 4))
        out.append((pos * 1000000 // rate, bytes(payload)))
        pos += n
    return out

# ---------------- parse the source file into groups of packets ----------------

class Group:
    __slots__ = ('ts', 'counter', 'chunks')           # chunks: list of raw (header+payload) OR
    def __init__(self, ts, counter):                  #   ('pkt', si, endframe, raw_bytes, payload_span)
        self.ts = ts; self.counter = counter; self.chunks = []

def parse(data):
    n = len(data); pos = 0; size = None
    groups = []; desc = None; blocksize = None
    idx_chunks = []                                    # (group#, chunk#) of seek-index chunks
    max_si = 0
    g = None
    while pos + 2 <= n:
        if data[pos] == 0x4C and data[pos + 1] == 0x32:
            ts = int.from_bytes(data[pos + 4:pos + 12], 'big')
            size = int.from_bytes(data[pos + 12:pos + 14], 'big') + 1
            blocksize = size
            i = pos + 14
            dstart = i
            while True:
                estart = i
                t, i = rvarb(data, i); ss, i = rvarb(data, i); i += ss
                if t == 0:
                    dend = estart          # terminator excluded: pack() writes its own
                    break
            if desc is None:
                desc = bytes(data[dstart:dend])
            g = Group(ts, data[i] & 0xFC)
            groups.append(g)
            flags = data[i]; ci = i + 1
            if flags & 2:
                ci += 2
        else:
            flags = data[pos]; ci = pos + 1
            if flags & 2:
                ci += 2
        end = pos + size
        while ci < end and data[ci] != 0:
            br = BitReader(data, ci)
            bits = br.poplen(); si = br.popn(bits); ef = br.pop()
            if ef:
                b = br.poplen(); br.popn(b); br.pop()
                b2 = br.poplen(); br.popn(b2 * 2 + 26)
            pkt = br.popn(13) + 1
            po = br.after()
            raw = bytes(data[ci:po + pkt])
            if si == 1:
                idx_chunks.append((len(groups) - 1, len(g.chunks), po - ci, pkt))
            g.chunks.append((si, raw))
            max_si = max(max_si, si)
            ci = po + pkt
        pos += size
    if pos != n:
        raise ValueError('block chain did not reach EOF — corrupt/unsupported')
    return groups, desc, blocksize, idx_chunks, max_si

# ---------------- repack groups into a block stream ----------------

def pack(groups, desc, blocksize):
    """Emit the block stream; returns (bytes, sync_offsets {group# -> file offset},
    payload_map: for every chunk, list of (file_offset_of_payload_start))."""
    out = bytearray()
    sync_off = {}
    chunk_pos = {}                                     # (g#, c#) -> payload file offset
    for gi, g in enumerate(groups):
        sync_off[gi] = len(out)
        tsb = g.ts.to_bytes(8, 'big')
        hdr = bytearray()
        hdr += b'\x4C\x32'
        hdr += sync_check(tsb).to_bytes(2, 'big')
        hdr += tsb
        hdr += (blocksize - 1).to_bytes(2, 'big')
        hdr += desc + b'\x00\x00'                      # descriptor list + type-0 terminator
        block = bytearray(hdr)
        block.append(g.counter)                        # flags byte (low bits 0 in native files)
        for ci, (si, raw) in enumerate(g.chunks):
            if len(block) + len(raw) + 1 > blocksize:  # +1: room for the terminator byte
                block += b'\x00' * (blocksize - len(block))
                out += block
                block = bytearray()
                block.append(g.counter)                # continuation block: flags byte only
            # payload offset inside the raw chunk = header length
            br = BitReader(raw, 0)
            bits = br.poplen(); br.popn(bits); ef = br.pop()
            if ef:
                b = br.poplen(); br.popn(b); br.pop()
                b2 = br.poplen(); br.popn(b2 * 2 + 26)
            br.popn(13)
            chunk_pos[(gi, ci)] = len(out) + len(block) + br.after()
            block += raw
        if len(block) < blocksize:
            block += b'\x00' * (blocksize - len(block))
        out += block
    return bytes(out), sync_off, chunk_pos

# ---------------- main ----------------

def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__); sys.exit(1)
    src, dst = args[0], args[1]
    wav_path = wav2_path = srt_path = None; audio_first = False; strip = False; norm = False
    i = 2
    while i < len(args):
        if args[i] == '--audio': wav_path = args[i + 1]; i += 2
        elif args[i] == '--audio2': wav2_path = args[i + 1]; i += 2
        elif args[i] == '--srt': srt_path = args[i + 1]; i += 2
        elif args[i] == '--audio-first': audio_first = True; i += 1
        elif args[i] == '--strip-audio': strip = True; i += 1
        elif args[i] == '--normalize': norm = True; i += 1
        else: print('unknown arg', args[i]); sys.exit(1)

    data = open(src, 'rb').read()
    groups, desc, blocksize, idx_chunks, max_si = parse(data)
    print(f'  source: {len(data)} B, {len(groups)} sync groups, block {blocksize}, streams<= {max_si}')

    new_desc = bytearray(desc)
    next_si = max_si + 1

    if strip:
        # remove the existing audio stream: its packets AND its descriptor entry. The freed
        # stream number is NOT reused for safety -- new tracks get fresh indices, and the
        # official player sees the first audio DESCRIPTOR, which is the one we add.
        dropped = 0
        nd = bytearray(); j = 0
        audio_sis = set()
        while j < len(new_desc):
            t, j2 = rvarb(new_desc, j); ss, j3 = rvarb(new_desc, j2)
            if t == 2:
                audio_sis.add(new_desc[j3])   # first payload byte of a type-2 entry = stream index
                j = j3 + ss
                continue
            nd += new_desc[j:j3 + ss]
            j = j3 + ss
        new_desc = nd
        for g in groups:
            kept = [(si, raw) for (si, raw) in g.chunks if si not in audio_sis]
            dropped += len(g.chunks) - len(kept)
            g.chunks = kept
        print(f'  stripped existing audio: streams {sorted(audio_sis)}, {dropped} packets removed')

    def load_wav(path):
        w = wave.open(path, 'rb')
        assert w.getsampwidth() == 2, 'need 16-bit PCM'
        chn, rate = w.getnchannels(), w.getframerate()
        pcm = list(struct.unpack(f'<{w.getnframes() * chn}h', w.readframes(w.getnframes())))
        w.close()
        if norm:
            # Peak-normalize to -1 dBFS: scale = target/peak, so the loudest sample lands ON the
            # target and nothing can exceed it -- clipping is impossible by construction, with
            # ~10%% headroom left for ADPCM transient overshoot (decoder clamps regardless).
            peak = max(1, max(pcm), -min(pcm))
            target = 29204                              # -1.0 dBFS
            if peak != target:
                sc = target / peak
                pcm = [int(x * sc) for x in pcm]
                db = __import__('math').log10(sc) * 20
                print(f'    normalize {path.split("/")[-1]}: peak {peak} -> {target} ({db:+.1f} dB)')
        return pcm, chn, rate

    def interleave(apkts, asi, rate):
        spans = [g.ts for g in groups]
        dur_pkt = PKT_SAMPLES * 1000000 // rate
        gi = 0
        for (t_us, payload) in apkts:
            t_end = t_us + dur_pkt
            while gi + 1 < len(groups) and spans[gi + 1] <= t_end + 1:
                gi += 1
            efv = max(0, t_end - (spans[gi] - 1))       # sync ts is content ts + 1
            raw = chunk_header(asi, 1, efv, len(payload), 0) + payload
            groups[gi].chunks.append((asi, raw))

    # ---- added audio stream(s) ----
    if wav_path:
        pcm, chn, rate = load_wav(wav_path)
        apkts = adpcm_packets(pcm, chn, rate)
        if audio_first and max_si == 2:
            # The file's ONE existing audio stream (si=2) moves to si=3; the new track takes 2.
            # si 2 ("10") and 3 ("11") are both two bits wide, so retagging is a one-bit patch
            # at a fixed position: header = [unary len "01"][si bits] -> bit 3 of the first byte.
            asi = 2; moved = 3; next_si = 4
            for g in groups:
                for k, (si, raw) in enumerate(g.chunks):
                    if si == 2:
                        rb = bytearray(raw); rb[0] |= 0x10   # "10"->"11" at bits 2-3 of byte 0
                        g.chunks[k] = (moved, bytes(rb))
            # descriptor list: retag the old audio entry's stream index byte 2 -> 3
            nd = bytearray(); j = 0
            while j < len(new_desc):
                t, j2 = rvarb(new_desc, j); ss, j3 = rvarb(new_desc, j2)
                entry = bytearray(new_desc[j:j3 + ss])
                if t == 2 and entry[j2 - j] == 2:
                    entry[j2 - j] = 3
                nd += entry
                j = j3 + ss
            new_desc = nd
        else:
            asi = next_si; next_si += 1
        new_desc += bytes([0x02, 0x06, asi, 0x01]) + (rate - 1).to_bytes(3, 'big') + bytes([chn - 1])
        print(f'  audio 1: {len(pcm)//chn} samples @{rate}Hz x{chn} -> {len(apkts)} packets as stream {asi}')
        interleave(apkts, asi, rate)

    if wav2_path:
        pcm2, chn2, rate2 = load_wav(wav2_path)
        apkts2 = adpcm_packets(pcm2, chn2, rate2)
        asi2 = next_si; next_si += 1
        new_desc += bytes([0x02, 0x06, asi2, 0x01]) + (rate2 - 1).to_bytes(3, 'big') + bytes([chn2 - 1])
        print(f'  audio 2: {len(pcm2)//chn2} samples @{rate2}Hz x{chn2} -> {len(apkts2)} packets as stream {asi2}')
        interleave(apkts2, asi2, rate2)

    # ---- embedded subtitles: one data-stream frame right after the seek index ----
    if srt_path:
        srt = open(srt_path, 'rb').read()
        ssi = next_si; next_si += 1
        new_desc += bytes([0x04, 0x02, ssi, 0x00])
        head = groups[idx_chunks[-1][0]] if idx_chunks else groups[0]
        CH = 1800
        off = 0
        while off < len(srt):
            part = srt[off:off + CH]
            off += CH
            last = off >= len(srt)
            raw = chunk_header(ssi, 1 if last else 0, 1, len(part), 1) + part
            head.chunks.append((ssi, raw))
        print(f'  subtitles: {len(srt)} B as stream {ssi} in {(len(srt)+CH-1)//CH} chunks at the head')

    # ---- repack with the widened descriptor list ----
    out, sync_off, chunk_pos = pack(groups, bytes(new_desc), blocksize)
    out = bytearray(out)

    # ---- patch the seek index: same entries, offsets remapped to the new block positions ----
    if idx_chunks:
        blob = bytearray()
        for (gi, ci, hlen, plen) in idx_chunks:
            raw = groups[gi].chunks[ci][1]
            blob += raw[hlen:hlen + plen]
        cnt, frames = struct.unpack('<II', blob[0:8])
        old_syncs = {}                                  # old file offset -> group#
        pos = 0; size = None; g = -1
        for gi2, grp in enumerate(groups):
            pass
        # rebuild old offset map by re-walking the ORIGINAL file
        pos = 0; size = None; gnum = -1
        while pos + 2 <= len(data):
            if data[pos] == 0x4C and data[pos + 1] == 0x32:
                size = int.from_bytes(data[pos + 12:pos + 14], 'big') + 1
                gnum += 1
                old_syncs[pos] = gnum
            pos += size
        for k in range(cnt):
            o = 16 + 24 * k
            f_, t_, off_ = struct.unpack('<QQQ', blob[o:o + 24])
            if off_ in old_syncs:
                blob[o + 16:o + 24] = struct.pack('<Q', sync_off[old_syncs[off_]])
        # scatter the patched blob back into the packed output
        w = 0
        for (gi, ci, hlen, plen) in idx_chunks:
            p = chunk_pos[(gi, ci)]
            out[p:p + plen] = blob[w:w + plen]
            w += plen
    open(dst, 'wb').write(out)
    print(f'wrote {dst}: {len(out)} B ({len(out)-len(data):+} B)')

if __name__ == '__main__':
    main()
