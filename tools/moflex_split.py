#!/usr/bin/env python3
"""moflex_split — cut a 3D source video into chunks for parallel Mobiclip encoding.

The source is a single full-SBS (left|right) or over-under (top/bottom) MP4 — every
frame already carries BOTH eyes, so any frame-boundary cut is pair-safe. This splits
losslessly (-c copy) at keyframes, so each chunk starts on a keyframe and is
independently decodable — exactly what the Nintendo encoder needs as input.

Encode each chunk separately (in parallel via sandboxed GUI instances), then join the
resulting .moflex segments with moflex_combine.py. Each independently-encoded chunk
starts Left-first with an even frame count, so 3D eye-parity stays correct at every seam.

Usage:
  moflex_split.py source.mp4 out_dir --parts 6            # ~6 roughly-equal chunks
  moflex_split.py source.mp4 out_dir --seconds 180        # ~3-minute chunks
  moflex_split.py source.mp4 out_dir --parts 6 --ffmpeg /path/to/ffmpeg

Notes:
  - Cuts land on the nearest source keyframe, so chunk lengths are approximate (fine —
    the combiner handles any durations; this only balances the parallel encode load).
  - Nothing is re-encoded, so there is zero quality loss before the Mobiclip encode.
"""
import sys, os, subprocess, argparse, json, shutil

def probe_duration(ffmpeg, src):
    # ffprobe sits next to ffmpeg in most builds; fall back to parsing ffmpeg -i
    ffprobe = os.path.join(os.path.dirname(ffmpeg), "ffprobe")
    if os.path.exists(ffprobe):
        out = subprocess.run([ffprobe, "-v", "quiet", "-print_format", "json",
                              "-show_format", src], capture_output=True, text=True)
        try: return float(json.loads(out.stdout)["format"]["duration"])
        except Exception: pass
    out = subprocess.run([ffmpeg, "-hide_banner", "-i", src], capture_output=True, text=True)
    for line in out.stderr.splitlines():
        if "Duration:" in line:
            h, m, s = line.split("Duration:")[1].split(",")[0].strip().split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    raise RuntimeError("could not determine source duration")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source"); ap.add_argument("out_dir")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--parts", type=int); g.add_argument("--seconds", type=float)
    ap.add_argument("--ffmpeg", default=shutil.which("ffmpeg") or "ffmpeg")
    a = ap.parse_args()

    os.makedirs(a.out_dir, exist_ok=True)
    dur = probe_duration(a.ffmpeg, a.source)
    seg = a.seconds if a.seconds else dur / a.parts
    ext = os.path.splitext(a.source)[1] or ".mp4"
    pat = os.path.join(a.out_dir, "chunk_%03d" + ext)
    print(f"source {dur:.1f}s -> segment_time {seg:.1f}s")

    cmd = [a.ffmpeg, "-hide_banner", "-y", "-i", a.source, "-c", "copy", "-map", "0",
           "-f", "segment", "-segment_time", f"{seg:.3f}", "-reset_timestamps", "1", pat]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr[-2000:]); sys.exit("ffmpeg split failed")

    chunks = sorted(f for f in os.listdir(a.out_dir) if f.startswith("chunk_"))
    print(f"wrote {len(chunks)} chunks to {a.out_dir}/")
    for c in chunks:
        p = os.path.join(a.out_dir, c)
        print(f"  {c}  {os.path.getsize(p)/1048576:.1f} MB")
    print("\nnext: encode each chunk to .moflex, then:")
    print(f"  moflex_combine.py combine movie.moflex {a.out_dir}/chunk_000.moflex ...")

if __name__ == "__main__":
    main()
