#!/usr/bin/env python3
# make3d.py -- turn a 2D moflex whose frames are already interleaved L,R,L,R
# (a normal 2D encode of a frame-sequential video) into a 3D moflex, by halving
# the declared video timebase so the player's 2x-frame-rate test detects 3D.
import sys, struct

def rvarb(d, i):
    v = d[i]; i += 1
    if not (v & 0x80): return v, i
    val = (v & 0x7f) << 7; v = d[i]; i += 1
    if not (v & 0x80): return val | v, i
    val = (((v & 0x7f) | val) << 7); v = d[i]; i += 1
    if not (v & 0x80): return val | v, i
    val = (((v & 0x7f) | val) << 7) | d[i]; i += 1
    return val, i

def main():
    if len(sys.argv) < 3:
        print("usage: make3d.py in.moflex out.moflex"); return 1
    d = bytearray(open(sys.argv[1], "rb").read())
    if len(d) < 16 or d[0] != 0x4C or d[1] != 0x32:
        print("not a moflex (missing 0x4C32 sync)"); return 1
    i = 2 + 2 + 8 + 2          # magic, skip2, ts(8), size(2)
    vt_off = vt_den = vt_num = None
    while True:
        typ, i = rvarb(d, i); ssize, i = rvarb(d, i)
        if typ == 0: break
        if typ == 2:            # audio: si, codec, rate(3), ch
            i += 6
        elif typ in (1, 3):     # video: si, codec, tb_den(2), tb_num(2), w(2), h(2), +skip
            si, codec = d[i], d[i+1]
            vt_den = struct.unpack(">H", d[i+2:i+4])[0]
            vt_off = i + 4
            vt_num = struct.unpack(">H", d[i+4:i+6])[0]
            w = struct.unpack(">H", d[i+6:i+8])[0]; h = struct.unpack(">H", d[i+8:i+10])[0]
            print(f"video stream {si}: {w}x{h}, timebase {vt_den}/{vt_num} = {vt_den/vt_num:.3f} fps")
            i += 10 + (3 if typ == 3 else 2)
        elif typ == 4:          # data
            i += 2
        else:
            print("unknown descriptor type", typ); return 1
    if vt_off is None:
        print("no video stream found"); return 1
    if vt_num * 2 <= 0xFFFF:
        struct.pack_into(">H", d, vt_off, vt_num * 2)
        newfps = vt_den / (vt_num * 2)
    elif vt_den % 2 == 0:
        struct.pack_into(">H", d, vt_off - 2, vt_den // 2)
        newfps = (vt_den // 2) / vt_num
    else:
        print("cannot halve timebase cleanly"); return 1
    open(sys.argv[2], "wb").write(d)
    print(f"patched -> {newfps:.3f} fps display rate; wrote {sys.argv[2]}")
    return 0

sys.exit(main())
