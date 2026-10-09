#!/usr/bin/env python3
"""make_super_moflex — one command from (source video + encoded moflex) to a SUPER MOFLEX.

Given the original MP4/MKV (dual audio + subtitles) and the mobiclip-encoded .moflex:
  1. extracts the English and original-language audio (44.1kHz stereo WAV)
  2. extracts EVERY subtitle track (language-coded; eng/sdh/sgn + cas for Castilian)
  3. fetches info from TMDB (needs your own TMDB token -- see TMDB_TOKEN below): title, year, genres,
     runtime, description -- and for TV episodes the episode title/air date/synopsis,
     plus the poster
  4. loudness-normalizes both audio tracks to -16 LUFS (EBU R128 two-pass, TP -1.0, LRA 11)
     -- the SAME target smoflex_build.py uses and the library was rebuilt to
  5. rebuilds the moflex: normalized ENG in-band (official player plays this), original
     audio + all subtitles + library info + poster in the CSXTRA trailer

Usage:
  make_super_moflex.py <source.mkv|mp4> <encoded.moflex> [options]
    -o OUT           output path (default: ./super/ next to the encoded file, named by the
                     catalog convention with the SUPER marker:
                       Movie Name (YEAR) (3D) (S).moflex
                       Show Name (YEAR) (3D) (S) - S01e01 - Episode Title.moflex)
    --format F       3D origin: native | converted (asked interactively when not given)
    --audio N        stream index for the IN-BAND track (the one the official player gets).
                     Without it the first eng-tagged track wins, which is wrong for a film
                     with no English dub but two English commentary tracks.
    --audio2 N|none  stream index for the trailer (second) audio, or 'none' for no second track
    --audio2-lang X  three-letter tag shown on the player's audio button for that track. Use it
                     when the track is not what its language tag implies -- a COMMENTARY is
                     tagged eng, and labelling it ENG invites someone to press it expecting a
                     dub. COM says what it is.
    --art FILE       use this poster instead of the one TMDB would fetch
    --add-genres G   comma-separated genres to ADD to what TMDB reports (batch-safe;
                     duplicates are ignored), e.g. 'Studio Ghibli, Anime'
    --subs A,B,C     keep ONLY these subtitle STREAM INDICES. Default is every text
                     track, which is wrong for a WEB-DL carrying 40 of them (19 of
                     them forced), and for scripts the player has no glyphs for --
                     Chinese renders as holes, since the 16x16 table is JIS kanji.
    --tmdb-se S:E    look the episode up at these TMDB coordinates instead of the ones in the
                     filename. Some shows are numbered continuously there: Re:Zero is one
                     85-episode "season 1", so its S02e01 is TMDB season 1 episode 26.
    --srt FILE       extra external subtitle (repeatable; language from '.xxx.srt' name).
                     An '.xxx.ass' / '.xxx.ssa' is accepted too and embedded as ASS (cleaned).
    --flatten-ass    flatten ASS/SSA tracks to SRT instead of embedding them as ASS. ASS is the
                     DEFAULT since player build 61008.1 (ASS/SSA rendering): the track is cleaned
                     by ass_to_srt.write_ass (karaoke layers, drawings, fonts gone; styles, signs
                     and \an kept). Use this only for a file meant for players OLDER than that --
                     they read only SRT and show NO subtitles for an ASS track.
    --keep-ass       accepted for old command lines; ASS is already the default
    --offset SECS    slide the SOURCE audio (and the source's own subtitle tracks) onto the
                     encode's timeline -- for when the moflex was NOT encoded from this file.
                     Positive = the encode starts earlier (extra head the source lacks).
                     MEASURE it: dump the encode's audio with pc_verify/test_audio and
                     cross-correlate against the source track; never eyeball it.
    --sub-offset S   same, but for --srt files only (they are usually timed to the encode's
                     master already, so this defaults to 0 while --offset does not apply to them)
    --lang X         three-letter tag for the IN-BAND track. A source that tags its audio
                     'und' (most MP4 rips do) would otherwise ship tagged UND, and English is
                     always the base track, so name it: --lang eng
    --title T        override the parsed title      --year Y   override the parsed year
    --yes            accept fetched info without prompting
    --no-net         skip TMDB (prompts for info; still builds audio/subs)

Requires: ffmpeg/ffprobe; moflex_addstreams.py, ass_to_srt.py and srt_clean.py beside this file.
TMDB lookups need your own token: TMDB_TOKEN=... or ~/.config/moflex-encoder/tmdb_token.
"""
import json
import os
import re
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.realpath(__file__))   # helpers + moflex_addstreams.py live beside this
# TMDB "API Read Access Token" (v4), from https://www.themoviedb.org/settings/api -- your OWN; none
# ships with this repo. TMDB_TOKEN in the environment, else the first line of
# ~/.config/moflex-encoder/tmdb_token. Without one the build runs as --no-net (asks for the info).
TMDB_TOKEN_FILE = os.path.expanduser('~/.config/moflex-encoder/tmdb_token')
def load_tmdb_token():
    t = os.environ.get('TMDB_TOKEN', '').strip()
    if not t and os.path.exists(TMDB_TOKEN_FILE):
        t = (open(TMDB_TOKEN_FILE).readline() or '').strip()
    return t
TMDB_TOKEN = load_tmdb_token()
ADDSTREAMS = os.path.join(HERE, 'moflex_addstreams.py')
MAX_CUT = -1.0        # never attenuate a master by more than this (dB)
CACHE = os.environ.get('SUPER_MOFLEX_CACHE', os.path.expanduser('~/.super_moflex_cache.json'))

def src_duration(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                        '-of', 'default=nk=1:nw=1', path], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 1e9                      # unknown: do not clip the tail

_TS_RE = re.compile(r'(\d\d):(\d\d):(\d\d)[,.](\d\d\d)')

def shift_srt(src, dst, offset):
    """Rewrite an .srt with every cue moved by `offset` seconds."""
    off_ms = int(round(offset * 1000))

    def bump(m):
        ms = (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))) * 1000 + int(m.group(4))
        ms = max(0, ms + off_ms)
        return f'{ms//3600000:02d}:{ms//60000%60:02d}:{ms//1000%60:02d},{ms%1000:03d}'

    body = open(src, encoding='utf-8', errors='replace').read()
    open(dst, 'w', encoding='utf-8').write(_TS_RE.sub(bump, body))
    return dst

_ASS_TS_RE = re.compile(r'^(Dialogue:[^,]*,)(\d+:\d\d:\d\d\.\d\d),(\d+:\d\d:\d\d\.\d\d),', re.M)

def shift_ass(src, dst, offset):
    """shift_srt for ASS: move every Dialogue line's Start and End by `offset` seconds.
    Assumes the standard Format (Layer, Start, End, ...) -- the order write_ass() keeps."""
    off_cs = int(round(offset * 100))

    def one(v):
        h, m, rest = v.split(':'); sec, cs = rest.split('.')
        t = max(0, ((int(h) * 60 + int(m)) * 60 + int(sec)) * 100 + int(cs) + off_cs)
        return f'{t//360000}:{t//6000%60:02d}:{t//100%60:02d}.{t%100:02d}'

    body = open(src, encoding='utf-8', errors='replace').read()
    body = _ASS_TS_RE.sub(lambda m: f'{m.group(1)}{one(m.group(2))},{one(m.group(3))},', body)
    open(dst, 'w', encoding='utf-8').write(body)
    return dst

def tmdb(path, **params):
    q = '&'.join(f'{k}={urllib.request.quote(str(v))}' for k, v in params.items())
    req = urllib.request.Request(f'https://api.themoviedb.org/3/{path}?{q}' if q
                                 else f'https://api.themoviedb.org/3/{path}')
    req.add_header('Authorization', f'Bearer {TMDB_TOKEN}')
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)

def parse_name(fn):
    """title, year, (season, episode) from a release-style filename."""
    b = os.path.basename(fn)
    b = re.sub(r'\.(mkv|mp4|moflex)$', '', b, flags=re.I)
    ep = re.search(r'[. _]S(\d+)E(\d+)', b, re.I)
    se = (int(ep.group(1)), int(ep.group(2))) if ep else None
    head = b[:ep.start()] if ep else b
    ym = re.search(r'[(. _](19\d\d|20\d\d)[). _]', head)
    year = int(ym.group(1)) if ym else 0
    if ym:
        head = head[:ym.start()]
    title = re.sub(r'[._]+', ' ', head).strip(' -')
    return title, year, se

def probe(src):
    return json.loads(subprocess.run(
        ['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', src],
        capture_output=True, text=True).stdout)['streams']

def sub_code(lang, title):
    t = (title or '').lower()
    if lang == 'eng':
        if 'sign' in t or 'forced' in t: return 'sgn'
        # A DUBTITLE is a transcript of the English dub; SDH is for the hard of hearing. They
        # are different things and a viewer wants them in different situations, so they no
        # longer share a code. With Japanese audio selected the dubtitle is the WRONG text --
        # it says what the dub says, not what was said.
        if 'dubtitle' in t or 'dub' in t: return 'dub'
        if 'sdh' in t: return 'sdh'
        return 'eng'
    if lang == 'spa':
        return 'cas' if 'castilian' in t else 'spa'
    return (lang or 'und')[:3]

def fold_meta(s):
    """Fold library-info text to what the player can actually draw.

    Subtitles go through srt_clean; the NFO did not, so a TMDB synopsis put U+201C/U+2019/U+2026
    straight into the description. The player's font tables are ASCII + Latin-1 + Greek/Turkish,
    so a curly quote or an ellipsis has NO GLYPH and renders as a hole. Accented Latin-1 is
    fine and is deliberately left alone.
    """
    if not isinstance(s, str) or not s:
        return s
    try:
        sys.path.insert(0, HERE)
        import srt_clean
    except Exception:
        return s
    out = []
    for c in s:
        c = srt_clean.C1.get(c, c)
        if c in srt_clean.DROP:
            continue
        out.append(srt_clean.FOLD.get(c, c))
    return ''.join(out)


def scrub(path):
    """Clean a subtitle IN PLACE with the Ghibli-batch cleaner, if it is available.

    Embedding a downloaded or muxed subtitle unexamined is how 82 advert cues, 68 ASS override
    tags and 13,652 mojibake characters reached finished files. Doing it here means every build
    gets it, instead of a repair pass afterwards on files already shipped.
    """
    try:
        sys.path.insert(0, HERE)
        import srt_clean
    except Exception:
        return 'not cleaned -- srt_clean unavailable'
    try:
        text, enc = srt_clean.read_any(path)
        out, stats = srt_clean.clean(text)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(out)
        bits = [f'{k}:{v}' for k, v in sorted(stats.items()) if v]
        if enc.lower() not in ('utf-8', 'utf-8-sig'):
            bits.insert(0, f'was {enc}')
        return ' '.join(bits)
    except Exception as ex:
        return f'clean failed: {ex}'


def ask(prompt, default, yes):
    if yes:
        return default
    v = input(f'{prompt} [{default}]: ').strip()
    return v or default

def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__); sys.exit(1)
    src, enc = args[0], args[1]
    out = None; outdir = None; extra_srts = []; o_title = o_year = None; yes = False; net = True
    offset = 0.0; sub_offset = 0.0
    o_format = None; o_3d = None
    a_main = a_second = None; o_art = None; a2_lang = None; tmdb_se = None; o_genres = ''
    o_lang = None
    only_subs = None
    keep_ass = True                      # player 61008.1+ renders ASS/SSA; --flatten-ass opts out
    i = 2
    while i < len(args):
        if args[i] == '-o': out = args[i + 1]; i += 2
        elif args[i] == '--outdir': outdir = args[i + 1]; i += 2
        elif args[i] == '--srt': extra_srts.append(args[i + 1]); i += 2
        elif args[i] == '--title': o_title = args[i + 1]; i += 2
        elif args[i] == '--year': o_year = int(args[i + 1]); i += 2
        elif args[i] == '--offset': offset = float(args[i + 1]); i += 2
        elif args[i] == '--sub-offset': sub_offset = float(args[i + 1]); i += 2
        elif args[i] == '--yes': yes = True; i += 1
        elif args[i] == '--keep-ass': keep_ass = True; i += 1
        elif args[i] == '--flatten-ass': keep_ass = False; i += 1
        elif args[i] == '--no-net': net = False; i += 1
        elif args[i] == '--format': o_format = args[i + 1]; i += 2
        elif args[i] == '--3d': o_3d = 1; i += 1
        elif args[i] == '--2d': o_3d = 0; i += 1
        elif args[i] == '--audio': a_main = int(args[i + 1]); i += 2
        elif args[i] == '--audio2': a_second = int(args[i + 1]) if args[i+1] != 'none' else -1; i += 2
        elif args[i] == '--art': o_art = args[i + 1]; i += 2
        elif args[i] == '--add-genres': o_genres = args[i + 1]; i += 2
        elif args[i] == '--subs':
            only_subs = {int(x) for x in args[i + 1].split(',') if x.strip()}; i += 2
        elif args[i] == '--audio2-lang': a2_lang = args[i + 1][:3].upper(); i += 2
        elif args[i] == '--lang': o_lang = args[i + 1][:3].lower(); i += 2
        elif args[i] == '--tmdb-se':
            _s, _e = args[i + 1].split(':'); tmdb_se = (int(_s), int(_e)); i += 2
        else: print('unknown arg', args[i]); sys.exit(1)
    workbase = outdir or os.path.join(os.path.dirname(os.path.abspath(enc)), 'super')
    os.makedirs(workbase, exist_ok=True)
    work = os.path.join(workbase, os.path.basename(enc) + '.work')
    os.makedirs(work, exist_ok=True)

    title, year, se = parse_name(src)
    if o_title: title = o_title
    if o_year: year = o_year
    print(f'== {title!r} year={year or "?"} ' + (f'S{se[0]:02d}E{se[1]:02d}' if se else '(movie)'))

    # ---- streams ----
    streams = probe(src)
    audio = [s for s in streams if s['codec_type'] == 'audio']
    subs = [s for s in streams if s['codec_type'] == 'subtitle']
    # --audio/--audio2 name stream indices outright, because "the eng-tagged one" is not
    # always the soundtrack. A film with no English dub can still carry two English COMMENTARY
    # tracks, and picking the first eng track then puts the director talking over the film as
    # the only audio the official player will ever play.
    by_index = {st['index']: st for st in audio}
    if a_main is not None:
        a_eng = by_index.get(a_main)
        if a_eng is None:
            print(f'  --audio {a_main} is not an audio stream in this file'); sys.exit(1)
        if a_second == -1:   a_orig = None
        elif a_second is not None:
            a_orig = by_index.get(a_second)
            if a_orig is None:
                print(f'  --audio2 {a_second} is not an audio stream'); sys.exit(1)
        else: a_orig = next((s for s in audio if s is not a_eng), None)
    else:
        a_eng = next((s for s in audio if s.get('tags', {}).get('language') == 'eng'), None)
        a_orig = next((s for s in audio if s is not a_eng), None)
    if not a_eng:
        a_eng, a_orig = (audio[0], None) if audio else (None, None)
        print('  NOTE: no eng-tagged audio; using the first track in-band, no second track')
    if not a_eng:
        print('  ERROR: source has no audio'); sys.exit(1)
    orig_lang = (a_orig.get('tags', {}).get('language', 'und')[:3].upper() if a_orig else None)
    if a2_lang: orig_lang = a2_lang   # what it IS, not what it is tagged

    # ---- extract audio ----
    # --offset slides everything taken from the source onto the encode's timeline, for the case
    # where the encode did NOT come from this exact file (a master with a longer head, etc).
    # Measure it, never guess: cross-correlate the encode's own audio (pc_verify/test_audio
    # dumps it) against the source track -- Howl's needed +14.446 s, flat across the whole film.
    pre, post = [], []
    if offset > 0:
        post = ['-af', f'adelay={int(round(offset * 1000))}:all=1']
    elif offset < 0:
        pre = ['-ss', f'{-offset:.3f}']
    if offset:
        post += ['-t', f'{src_duration(src):.3f}']   # silence in at the head, same off the tail

    def wav(stream, tag):
        """Extract and LOUDNESS-normalise, EBU R128 two-pass, I=-16 TP=-1.0 LRA=11.

        This is the same target smoflex_build.py uses, and the same the library was rebuilt to.
        It used to hand a raw WAV to moflex_addstreams --normalize, which peak-normalises to
        -1 dBFS -- a different thing entirely. Peak matches the loudest sample; loudness matches
        what a listener hears. A film with one loud explosion and quiet dialogue peak-normalises
        to quiet dialogue, and lands wherever it lands: Earthsea came out near -16 by luck and
        nothing was holding it there.

        Order matters: the downmix goes FIRST. `-ac 2` is an output option applied AFTER -af,
        so normalising then downmixing measures a different signal than the one written.
        """
        p = os.path.join(work, f'audio.{tag}.wav')
        if os.path.exists(p):
            return p
        head = 'aformat=sample_rates=44100:channel_layouts=stereo'
        ln = 'I=-16:TP=-1.0:LRA=11'
        meas = subprocess.run(['ffmpeg', '-hide_banner', '-nostats'] + pre + ['-i', src,
                               '-map', f"0:{stream['index']}",
                               '-af', f'{head},loudnorm={ln}:print_format=json',
                               '-f', 'null', '-'], capture_output=True, text=True).stderr
        m = re.search(r'\{[^{}]*"input_i"[^{}]*\}', meas, re.S)
        chain = [head]
        if m:
            j = json.loads(m.group(0))
            meas_i, meas_tp = float(j['input_i']), float(j['input_tp'])
            # ONE constant gain, and never the loudnorm filter itself.
            #
            # loudnorm with linear=true silently FALLS BACK TO DYNAMIC compression whenever the
            # gain needed to hit the target would breach the true-peak ceiling. The Wind Rises
            # needed +8.63 dB from -24.63 LUFS but had only 4.01 dB of peak headroom, so it was
            # compressed -- gain wandering between 1.97x and 4.33x within 90 seconds -- and that
            # is audible as crackle on top of an ADPCM encode. Nobody asked for compression; the
            # job is to change the VOLUME.
            #
            # So: take the smaller of "what reaches the target" and "what the peak allows". A
            # film that cannot reach -16 LUFS without squashing simply ends up quieter, which is
            # the right trade and is verifiable afterwards.
            #
            # And a FLOOR on the reduction. `room` goes negative on a hot master -- these
            # Halloween masters measure +1.1 to +2.1 dBTP -- and the ceiling was then quietly
            # turning the film DOWN by 2-3 dB, which is an audible loss on a handheld and puts
            # it well below the rest of the library. The only real reason to keep any headroom
            # is that IMA-ADPCM decode can overshoot a full-scale sample; that is worth a few
            # tenths, not three decibels. -1.0 dBTP was inherited from the loudnorm TARGET,
            # where it was a LIMITER ceiling, and it does not belong as a hard cap on a
            # constant gain. Never attenuate by more than MAX_CUT.
            want = -16.0 - meas_i
            room = -1.0 - meas_tp
            # Floor the PEAK constraint, not the result. max(min(want, room), MAX_CUT) also
            # clamps a legitimate loudness REDUCTION: a master already at -7.7 LUFS wants
            # -8.3 dB to reach the target and would have been held at -1.0, shipping ~7 dB
            # louder than the library. Only `room` -- the true-peak ceiling -- may be floored.
            gain = min(want, max(room, MAX_CUT))
            chain.append(f'volume={gain:.2f}dB')
            if abs(gain - MAX_CUT) < 0.01 and room < MAX_CUT <= want:
                note = (f' (hot master {meas_tp:+.2f} dBTP; reduction held at {MAX_CUT:.1f} dB '
                        f'instead of {room:+.2f})')
            elif gain < want - 0.01:
                note = f' (capped from {want:+.2f} by true peak)'
            else:
                note = ''
            print(f'    gain {tag}: {meas_i:.2f} LUFS {gain:+.2f} dB -> {meas_i + gain:.2f} LUFS{note}')
        else:
            print(f'    gain {tag}: measurement failed -- leaving the level alone')
        if post:
            chain.append(post[1]) if post[0] == '-af' else None
        chain.append('aresample=44100')
        tail = [x for x in post if x not in ('-af',) and not x.startswith('adelay')]
        subprocess.run(['ffmpeg', '-y', '-v', 'error'] + pre + ['-i', src,
                        '-map', f"0:{stream['index']}", '-af', ','.join(chain),
                        '-ac', '2', '-ar', '44100'] + tail + ['-c:a', 'pcm_s16le', p], check=True)
        import wave
        with wave.open(p, 'rb') as h:
            got = (h.getframerate(), h.getnchannels(), h.getsampwidth())
        if got != (44100, 2, 2):
            raise SystemExit(f'{tag}: wrote {got}, expected (44100, 2, 2)')
        return p
    main_lang = (a_eng.get('tags', {}).get('language', 'eng')[:3].lower() if a_eng else 'eng')
    # An MP4 rip usually carries no language tag at all and ffprobe reports 'und'. Shipping that
    # puts UND on the player's audio button for what is plainly the English track, so an explicit
    # --lang wins, and a bare 'und' falls back to eng rather than being published as-is.
    if o_lang:
        main_lang = o_lang
    elif main_lang in ('und', ''):
        main_lang = 'eng'
        print("  NOTE: source tags its audio 'und'; tagging the in-band track ENG")
    eng_wav = wav(a_eng, main_lang)
    orig_wav = wav(a_orig, orig_lang.lower()) if a_orig else None
    print(f'  audio: eng ok' + (f', {orig_lang} ok' if orig_wav else ''))

    # ---- extract every subtitle ----
    srt_files = []                       # (code, path) -- eng first later
    seen = set()
    IMAGE_SUBS = {'hdmv_pgs_subtitle', 'dvd_subtitle', 'dvdsub', 'xsub', 'pgssub'}
    skipped_img = 0
    for st in subs:
        if only_subs is not None and st['index'] not in only_subs:
            continue                             # --subs: an explicit keep-list of stream indices
        if st.get('codec_name') in IMAGE_SUBS:   # PGS/VobSub -> images, need OCR (not text)
            skipped_img += 1; continue
        tags = st.get('tags', {})
        c = sub_code(tags.get('language', 'und'), tags.get('title'))
        if c in seen:
            # Two tracks of the same language and no label to tell them apart. Dropping the
            # second was fine when it was a duplicate, and wrong the moment a release carried
            # a true translation AND a dubtitle both plainly tagged "English" -- one of the two
            # a viewer actually wants would just vanish. Keep it under its own code and say so;
            # which is which is a judgement for whoever can read them.
            alt = None
            for n in range(2, 6):
                cand = f'{c[:2]}{n}'
                if cand not in seen: alt = cand; break
            if not alt: continue
            print(f'  NOTE: a second {c!r} track (title={tags.get("title")!r}) kept as {alt!r} '
                  f'-- check which is the translation and which the dubtitle')
            c = alt
        p = os.path.join(work, f'subs.{c}.srt')
        if st.get('codec_name') in ('ass', 'ssa'):
            # Do NOT let ffmpeg flatten an ASS track. Anime releases typeset the opening as
            # per-FRAME karaoke -- Re:Zero S02E02 has 27,994 events each 0.04 s long drawing a
            # single letter -- and ffmpeg turns every one into a "cue". That episode came out
            # as 30,589 cues for 393 lines of dialogue. ass_to_srt drops the effect layers.
            a = os.path.join(work, f'subs.{c}.ass')
            r = subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', src,
                                '-map', f"0:{st['index']}", '-c:s', 'copy', a],
                               capture_output=True)
            if r.returncode == 0 and os.path.exists(a) and keep_ass:
                # default (no --flatten-ass): same filtering, still ASS. The language code stays right before
                # the extension -- addstreams reads the SUB1 tag from '.<lang>.ass'.
                sys.path.insert(0, HERE)
                import ass_to_srt
                p = os.path.join(work, f'subs.clean.{c}.ass')
                if ass_to_srt.write_ass(a, p) == 0:
                    os.remove(p)
            elif r.returncode == 0 and os.path.exists(a):
                try:
                    sys.path.insert(0, HERE)
                    import ass_to_srt
                    n = ass_to_srt.write_srt(ass_to_srt.convert(a), p)
                    if n == 0:
                        os.remove(p)
                except Exception as ex:
                    print(f'    ass_to_srt failed ({ex}); falling back to ffmpeg')
                    r = subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', src,
                                        '-map', f"0:{st['index']}", p], capture_output=True)
        else:
            r = subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', src,
                                '-map', f"0:{st['index']}", p], capture_output=True)
        if r.returncode == 0 and os.path.exists(p) and os.path.getsize(p) > 0:
            is_ass = p.endswith('.ass')
            body = open(p, encoding='utf-8', errors='replace').read()
            if is_ass:                       # srt_clean is an SRT cleaner: write_ass did this one
                cues, note = body.count('\nDialogue:'), 'ASS kept'
            else:
                cues, note = body.count(' --> '), scrub(p)
            print(f'    sub {c:<4} {cues:>5} cues   title={tags.get("title") or "(none)"}'
                  + (f'   [{note}]' if note else ''))
            if offset:                       # container subs share the source's timeline
                # The language code MUST stay immediately before .srt: addstreams infers the
                # SUB1 tag from a trailing '.<lang>.srt', so 'subs.eng.sync.srt' inferred
                # nothing and shipped the track tagged 'SUB' instead of 'ENG'.
                p = (shift_ass(p, os.path.join(work, f'subs.sync.{c}.ass'), offset) if is_ass
                     else shift_srt(p, os.path.join(work, f'subs.sync.{c}.srt'), offset))
            srt_files.append((c, p)); seen.add(c)
    if skipped_img:
        print(f'  NOTE: {skipped_img} image-based (PGS/VobSub) subtitle tracks skipped '
              f'-- they need OCR to become text. Supply text .srt via --srt to include subs.')
    for p in extra_srts:
        m = re.search(r'\.([a-z]{2,3})\.(srt|ass|ssa)$', p, re.I)
        c = (m.group(1).lower() if m else 'ext')
        is_ass = bool(re.search(r'\.(ass|ssa)$', p, re.I))
        if c not in seen:
            if is_ass:
                sys.path.insert(0, HERE)
                import ass_to_srt
                if keep_ass:                 # same cleaning the source's own ASS tracks get
                    q = os.path.join(work, f'subs.ext.{c}.ass')
                    n = ass_to_srt.write_ass(p, q)
                else:
                    q = os.path.join(work, f'subs.ext.{c}.srt')
                    n = ass_to_srt.write_srt(ass_to_srt.convert(p), q)
                if n == 0:
                    print(f'  NOTE: {p} has no usable dialogue -- skipped'); continue
                print(f'    sub {c:<4} {n:>5} cues   (external {"ASS" if keep_ass else "ASS->SRT"})')
                p, is_ass = q, keep_ass
            # supplied .srt files were timed to whatever release they were made for, which is
            # often the encode's master rather than this source -- hence a separate offset that
            # defaults to leaving them alone (Howl's: audio +14.446 s, downloaded subs correct)
            if sub_offset:                   # lang code last -- see the note above
                p = (shift_ass(p, os.path.join(work, f'subs.sync.ext.{c}.ass'), sub_offset) if is_ass
                     else shift_srt(p, os.path.join(work, f'subs.sync.ext.{c}.srt'), sub_offset))
            srt_files.append((c, p)); seen.add(c)
    # ENG first, then the rest of the English family, then JPN, then alphabetical. sdh/dub are
    # ENGLISH -- leaving them to sort alphabetically put Japanese first on a Disney film whose
    # only English track was tagged SDH, so the viewer's first subtitle was the wrong language.
    order = {'eng': 0, 'sdh': 1, 'dub': 2, 'sgn': 3, 'jpn': 4}
    srt_files.sort(key=lambda x: (order.get(x[0], 9), x[0]))
    print(f'  subtitles: {", ".join(c for c, _ in srt_files) or "none"}')

    # ---- TMDB info (cached per title so a 13-episode batch fetches the show once) ----
    info = {'title': title, 'year': year, 'category': 'TV Shows' if se else 'Movies',
            'genres': '', 'runtime': 0, 'date': '', 'desc': '', 'showdesc': '', 'poster': None,
            'eptitle': ''}
    cache = {}
    if os.path.exists(CACHE):
        cache = json.load(open(CACHE))
    key = f'{title}|{year}|{"tv" if se else "movie"}'
    if net and not TMDB_TOKEN:
        print('  no TMDB token (TMDB_TOKEN or ~/.config/moflex-encoder/tmdb_token) -- running as --no-net')
        net = False
    if net:
        try:
            show = cache.get(key)
            if not show:
                if se:
                    r = tmdb('search/tv', query=title, **({'first_air_date_year': year} if year else {}))
                    hit = r['results'][0]
                    det = tmdb(f"tv/{hit['id']}")
                    show = {'id': hit['id'], 'title': det['name'],
                            'year': int((det.get('first_air_date') or '0')[:4] or 0),
                            'genres': ', '.join(g['name'] for g in det['genres']),
                            'overview': det.get('overview', ''),
                            'runtime': (det.get('episode_run_time') or [23])[0],
                            'poster': det.get('poster_path')}
                else:
                    r = tmdb('search/movie', query=title, **({'year': year} if year else {}))
                    hit = r['results'][0]
                    det = tmdb(f"movie/{hit['id']}")
                    show = {'id': hit['id'], 'title': det['title'],
                            'year': int((det.get('release_date') or '0')[:4] or 0),
                            'genres': ', '.join(g['name'] for g in det['genres']),
                            'overview': det.get('overview', ''),
                            'runtime': det.get('runtime') or 0,
                            'poster': det.get('poster_path')}
                cache[key] = show
                json.dump(cache, open(CACHE, 'w'))
            info.update(title=show['title'], year=show['year'] or year,
                        genres=show['genres'], runtime=show['runtime'])
            # An explicit --title is an OVERRIDE, so it has to survive the fetch. TMDB carries
            # this show as "JUJUTSU KAISEN" in caps, which would shout in a catalogue where every
            # other entry is title case and would not match the show folder named for it.
            if o_title:
                info['title'] = o_title
            if se:
                info['showdesc'] = show['overview']
                # TMDB does not always number a show the way the files do. Re:Zero is one
                # continuous "season 1" of 85 episodes, so its Season 2 episode 1 is E26 --
                # asking for season/2 404s. --tmdb-se gives the lookup coordinates while the
                # filename and the printed "S2.E1" keep the numbering the viewer expects.
                lse = tmdb_se or se
                epk = f'{key}|E{lse[0]}x{lse[1]}'
                epi = cache.get(epk)
                if not epi:
                    epi = tmdb(f"tv/{show['id']}/season/{lse[0]}/episode/{lse[1]}")
                    cache[epk] = {'name': epi.get('name', ''), 'air_date': epi.get('air_date', ''),
                                  'overview': epi.get('overview', ''),
                                  'runtime': epi.get('runtime') or 0}
                    json.dump(cache, open(CACHE, 'w'))
                    epi = cache[epk]
                info['eptitle'] = epi['name']
                info['desc'] = (f'S{se[0]}.E{se[1]} \u201c{epi["name"]}\u201d - {epi["overview"]}'
                                if epi['name'] else info['showdesc'])
                info['date'] = epi['air_date']
                if epi.get('runtime'): info['runtime'] = epi['runtime']
            else:
                info['desc'] = show['overview']
            if show.get('poster'):
                pj = os.path.join(work, 'poster.jpg')
                if not os.path.exists(pj):
                    urllib.request.urlretrieve(f"https://image.tmdb.org/t/p/w500{show['poster']}", pj)
                info['poster'] = pj
            print(f"  TMDB: {info['title']} ({info['year']})  {info['genres']}  {info['runtime']}min")
        except Exception as ex:
            print(f'  TMDB lookup failed ({ex}); falling back to prompts')
            # With --yes the prompts below accept whatever is there, so a failed EPISODE lookup
            # would ship a file with no description and say nothing. For a batch of 50 that is
            # exactly the kind of silent hole worth stopping on.
            if se and yes:
                print('  *** episode metadata is required -- refusing to build without it.\n'
                      '      If TMDB numbers this show continuously, pass --tmdb-se S:E.')
                sys.exit(2)

    # ---- confirm / fill in ----
    info['title'] = ask('Title', info['title'], yes)
    info['year'] = int(ask('Year', info['year'], yes) or 0)
    info['desc'] = ask('Description', info['desc'], yes)
    if se:
        info['showdesc'] = ask('Show description', info['showdesc'], yes)
    print(f"  detected genres: {info['genres'] or '(none)'}")
    # --add-genres supplies the answer for a batch: with --yes the prompt takes its default, so
    # a collection tag like "Studio Ghibli" could only ever be added by hand before this.
    extra_g = ask('ADD genres (comma-separated, blank keeps as-is)', o_genres, yes)
    if extra_g:
        have = {g.strip().lower() for g in info['genres'].split(',')}
        add = [g.strip() for g in extra_g.split(',') if g.strip() and g.strip().lower() not in have]
        info['genres'] = ', '.join([info['genres']] + add).strip(', ') if add else info['genres']
    # 3D origin matters for the catalog: always asked unless --format was given
    info['format'] = (o_format or ask('Native 3D or a conversion? (native/converted)',
                                      'converted', False)).strip().lower()
    if info['format'] not in ('native', 'converted'):
        info['format'] = 'converted'
    info['runtime'] = int(ask('Runtime (min)', info['runtime'], yes) or 0)
    if o_art:
        info['poster'] = o_art            # what the user made beats what TMDB happens to hold
    if not info['poster']:
        pp = ask('Poster image path (blank = none)', '', yes)
        if pp: info['poster'] = pp

    def fat_clean(name):
        for bad, rep in (('/', ' - '), (':', ' -'), ('*', ''), ('?', ''), ('"', "'"),
                         ('<', ''), ('>', ''), ('|', '')):
            name = name.replace(bad, rep)
        return re.sub(r'\s+', ' ', name).strip()

    encl = os.path.basename(enc).lower()
    detected3d = ('(3d)' in encl or 'sbs' in encl or 'lrf' in encl or
                  'over-under' in encl or 'ou_' in encl or 'hsbs' in encl)
    if o_3d is not None:
        is3d = o_3d
    elif detected3d:
        is3d = 1
    else:                                    # no 3D marker in the name -> ask (default 2D)
        is3d = 1 if ask('3D video? (y/n)', 'n', yes).lower().startswith('y') else 0
    if not out:
        tag3d = ' (3D)' if is3d else ''
        if se:
            ep_title = info.get('eptitle', '')
            prefix = f"{info['title']} ({info['year']}){tag3d} (S) - S{se[0]:02d}e{se[1]:02d}"
            # keep the FULL path (sdmc:/tv/<showfolder>/<file>) safely under the 3DS FS ~255-char
            # limit: the show folder repeats the prefix, so cap the whole basename at 130 chars
            # and truncate only the episode title (at a word boundary) if needed.
            CAP = 130
            if ep_title:
                room = CAP - len(prefix) - len(" - ") - len(".moflex")
                if len(ep_title) > room > 0:
                    cut = ep_title[:room].rsplit(' ', 1)[0].rstrip(' -!?,')
                    ep_title = (cut or ep_title[:room]) + '\u2026'   # ellipsis marks truncation
            base = (f"{prefix}" + (f" - {ep_title}" if ep_title else '') + '.moflex')
        else:
            base = f"{info['title']} ({info['year']}){tag3d} (S).moflex"
        out = os.path.join(workbase, fat_clean(base))
        print(f'  output name: {os.path.basename(out)}')

    nfo = os.path.join(work, 'info.nfo')
    with open(nfo, 'w') as f:
        f.write(f"title={fold_meta(info['title'])}\nyear={info['year']}\n"
                f"category={info['category']}\ngenres={fold_meta(info['genres'])}\n"
                f"runtime={info['runtime']}\ndate={info['date']}\n"
                f"is3d={is3d}\nformat={info['format']}\ndesc={fold_meta(info['desc'])}\n")
        if info['showdesc']:
            f.write(f"showdesc={fold_meta(info['showdesc'])}\n")

    # ---- build ----
    cmd = [sys.executable, ADDSTREAMS, enc, out,
           '--strip-audio', '--audio', eng_wav,
           '--lang', main_lang.upper(), '--nfo', nfo]
    if orig_wav:
        cmd += ['--trailer-audio', orig_wav, '--trailer-lang', orig_lang]
    for c, p in srt_files:
        cmd += ['--trailer-srt', p]
    if info['poster']:
        cmd += ['--art', info['poster']]
    print('  building...')
    subprocess.run(cmd, check=True)
    # ---- post-write verification: a NAS/SMB flake once zeroed part of a trailer ----
    import struct as _st
    data = open(out, 'rb').read()
    assert data[-8:] == b'CSXTRA01', 'footer missing after write'
    poff = _st.unpack('<Q', data[-16:-8])[0]
    p2 = poff; known = {b'NFO0', b'ART5', b'LNG0', b'SUB0', b'SUB1', b'AUD0', b'AUD1'}
    while p2 + 8 <= len(data) - 16:
        cc = bytes(data[p2:p2 + 4]); ln = _st.unpack('<I', data[p2 + 4:p2 + 8])[0]
        assert cc in known, f'corrupt trailer section at {p2}'
        p2 += 8 + ln
    assert p2 == len(data) - 16, 'trailer walk did not land on the footer'
    print('  verified: trailer sections intact')
    # the full-size poster travels NEXT TO the movie, same name, .jpg extension
    if info['poster'] and os.path.exists(info['poster']):
        import shutil
        pj = re.sub(r'\.moflex$', '', out, flags=re.I) + '.jpg'
        shutil.copyfile(info['poster'], pj)
        print(f'  poster: {pj}')
    print(f'DONE: {out}')
    print(f'  (work files kept in {work} -- delete when happy)')

if __name__ == '__main__':
    main()
