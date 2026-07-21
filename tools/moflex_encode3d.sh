#!/bin/bash
# moflex_encode3d.sh <input_sbs.(mkv|mp4)> <qyx 0-8> <output.moflex> [ffmpeg]
# Encode a full-SBS (left|right) 3D source into a frame-interleaved .moflex that plays
# on BOTH the official 3DS player and the clownsec player.
#
# Pipeline (the baseline mobipeg encoder — to be replaced by our own, more efficient one):
#   1. framepack=frameseq : interleave L/R eyes into one L,R,L,R stream (400x240/eye)
#   2. mobipeg mobiclip    : -mobi_qyx N (bitrate knob), pcm->adpcm audio, layout 0
#   3. make3d.py           : halve the declared fps so the 2x-frame 3D detect fires
#   4. patch_ts.py         : share each L/R pair's timestamp (official player pairs by it)
#
# qyx: LOWER = higher bitrate = better quality. ~ qyx2:5Mbps  qyx3:3.2  qyx4:2  qyx5:1.4
# NOTE: mobipeg's MobiClip coder is ~3x less efficient than the official Actimagine one,
# so good quality (qyx3) is ~3.2Mbps and may stutter on Old-3DS. This is the gap our own
# encoder aims to close.
set -e
IN="$1"; QYX="$2"; OUT="$3"; FF="${4:-ffmpeg}"
HERE="$(cd "$(dirname "$0")" && pwd)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

"$FF" -hide_banner -y -i "$IN" -filter_complex \
"[0:v]crop=iw/2:ih:0:0,scale=400:240[l];[0:v]crop=iw/2:ih:iw/2:0,scale=400:240[r];[l][r]framepack=frameseq[v]" \
-map "[v]" -map 0:a -c:v mobiclip -mobi_qyx "$QYX" \
-c:a pcm_s16le -mo_audio adpcm -ar 44100 -ac 2 -mo_layout 0 "$TMP/1.moflex"
python3 "$HERE/make3d.py"  "$TMP/1.moflex" "$TMP/2.moflex"
python3 "$HERE/patch_ts.py" "$TMP/2.moflex" "$OUT"
echo "wrote $OUT"
