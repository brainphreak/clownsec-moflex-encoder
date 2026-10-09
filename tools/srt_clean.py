"""Normalise a subtitle file into something the player can actually draw.

    srt_clean.py file.srt [file.srt ...]     rewrites in place, keeps a .raw backup
    srt_clean.py --scan dir                  report only

Four things, all of which have bitten real files in this batch:

1. ENCODING. Plenty of subtitle uploads are Latin-1/CP1252, not UTF-8. Reading them as UTF-8
   with errors='replace' turns every accented character into U+FFFD -- 13,652 of them across
   the French, Spanish and German files here, and they went into shipped moflex that way. The
   encoding is detected, not assumed.

2. HOMOGLYPHS. Some uploads swap Latin letters for identical-looking Greek or Cyrillic ones to
   defeat duplicate detection. Invisible in a desktop font; on the 3DS the player draws Greek
   from a thinner face, so a line comes out with a few letters in the wrong weight.

3. TYPOGRAPHY THE FONT DOES NOT HAVE. The player covers ASCII, Latin-1, Greek and a little
   Turkish (ui_gfx.c), plus 16x16 CJK for subtitles. Curly quotes, em dashes and ellipses are
   in none of those and come out as '?' or nothing. They are folded to ASCII equivalents.

4. INVISIBLE CONTROL CHARACTERS. BOMs and bidi marks (U+202A and friends) that do nothing but
   confuse the renderer.

CJK is left completely alone -- the subtitle renderer has a real 16x16 kana/kanji/Hangul font.
"""
import os, re, sys, glob, collections

# Whole cues that are advertising, not dialogue. One or two ride along with almost every
# OpenSubtitles download -- an advert at the head, a credit at the tail -- and they display on
# screen six seconds into the film.
ADVERT = re.compile(
    r'opensubtitles|advertise your product|subtitles downloaded from|support us and become|'
    r'api\.opensubtitles|osdb\.link|subscene|yifysubtitles|addic7ed|podnapisi|'
    r'sync(?:ed|hronized)? and correc?ted by|corrected by|resync(?:ed)? by|'
    r'ripped by|encoded by|translated by .{0,30}(?:team|group|fansub)|'
    r'www\.[a-z0-9-]+\.[a-z]{2,}|https?://', re.I)

# Tokens that look like OCR damage: a letter word with a digit wedged into it, or an isolated
# quoted digit where a contraction belongs ("Don '2' be" should be "Don't be"). REPORTED, not
# silently rewritten -- guessing at somebody's dialogue is worse than leaving it visible.
SUSPECT = re.compile(r"\b(?=[A-Za-z]*[0-9])(?=[0-9]*[A-Za-z])[A-Za-z0-9']{2,}\b|'\s*[0-9]\s*'")


def strip_adverts(text):
    """Drop whole cues whose body is advertising. Cue numbers are renumbered afterwards."""
    blocks = re.split(r'\r?\n\r?\n', text)
    keep, dropped = [], 0
    for b in blocks:
        if not b.strip():
            continue
        lines = b.strip().splitlines()
        body = '\n'.join(lines[2:]) if len(lines) > 2 and '-->' in (lines[1] if len(lines) > 1 else '') else b
        if ADVERT.search(body):
            dropped += 1
            continue
        keep.append(b.strip())
    out = []
    for i, b in enumerate(keep, 1):
        lines = b.splitlines()
        if lines and lines[0].strip().isdigit():
            lines[0] = str(i)
        out.append('\n'.join(lines))
    return '\n\n'.join(out) + '\n', dropped

HOMO = {
    'Α':'A','Β':'B','Ε':'E','Ζ':'Z','Η':'H','Ι':'I','Κ':'K','Μ':'M','Ν':'N','Ο':'O',
    'Ρ':'P','Τ':'T','Υ':'Y','Χ':'X','α':'a','ε':'e','ι':'i','κ':'k','ν':'v','ο':'o',
    'ρ':'p','τ':'t','υ':'u','χ':'x','ς':'c','γ':'y','η':'n',
    'А':'A','В':'B','С':'C','Е':'E','Н':'H','К':'K','М':'M','О':'O','Р':'P','Т':'T',
    'Х':'X','У':'Y','а':'a','с':'c','е':'e','о':'o','р':'p','х':'x','у':'y','і':'i',
}
# characters the font has no glyph for, and what to use instead
FOLD = {
    '‘': "'", '’': "'", '‚': ",", '‛': "'",
    '“': '"', '”': '"', '„': '"', '‟': '"',
    '–': '-', '—': '-', '―': '-', '−': '-',
    '…': '...', ' ': ' ', '•': '*', '‹': '<', '›': '>',
    'ʼ': "'", '`': "'", '´': "'",
    # subtitle furniture the font has no glyph for; '?' is worse than a plain substitute
    '♪': '*', '♫': '*', '→': '->', '←': '<-', '≪': '<<', '≫': '>>', '※': '*',
    '　': ' ', '「': '"', '」': '"', '『': '"', '』': '"',
    'œ': 'oe', 'Œ': 'OE', 'æ': 'ae', 'Æ': 'AE', '€': 'EUR', '™': '(TM)',
    'š': 's', 'Š': 'S', 'ž': 'z', 'Ž': 'Z', 'ƒ': 'f', '†': '+', '‡': '+',
    '‰': '%', '˜': '~', 'ˆ': '^',
}
# CP1252 bytes 0x80-0x9F decoded as latin-1 land in the C1 control range and render as nothing.
# Map them back through the CP1252 table they came from.
C1 = {chr(0x80+i): c for i, c in enumerate(
    '\u20ac\x81\u201a\u0192\u201e\u2026\u2020\u2021\u02c6\u2030\u0160\u2039\u0152'
    '\x8d\u017d\x8f\x90\u2018\u2019\u201c\u201d\u2022\u2013\u2014\u02dc\u2122'
    '\u0161\u203a\u0153\x9d\u017e\u0178')}
DROP = {'﻿', '​', '‎', '‏', '‪', '‫', '‬',
        '‭', '‮', '⁦', '⁧', '⁨', '⁩', '­',
        '\x81', '\x8d', '\x8f', '\x90', '\x9d'}   # bytes cp1252 never defined


def read_any(path):
    """UTF-8 if it really is; otherwise CP1252, which covers the Western uploads. Never
    errors='replace' -- that is what silently produced the mojibake in the first place."""
    raw = open(path, 'rb').read()
    for enc in ('utf-8-sig', 'utf-8', 'cp1252', 'latin-1'):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode('latin-1', 'replace'), 'latin-1(lossy)'


def latin_ratio(t):
    letters = [c for c in t if c.isalpha()]
    return 1.0 if not letters else sum(1 for c in letters if ord(c) < 128) / len(letters)


def clean(t):
    stats = collections.Counter()
    # ASS/SSA override tags. The player strips <i> and <font ...> but NOT braces, so a
    # {\i1}...{\i0} pair renders LITERALLY on screen. 72 of them across this batch.
    t, nass = re.subn(r'\{\\[^}]*\}|\{[^}]{0,40}\}', '', t)
    if nass:
        stats['ass-tag'] += nass
    # <font face=...><b> wrappers. The player strips angle tags at moflex_playback.c:542 and
    # cannot render a font or italics anyway, so these are markup it throws away -- but
    # ffmpeg's ASS->SRT conversion wraps EVERY cue in them, which is HALF the bytes of a
    # converted track (65 KB -> 33 KB on a Re:Zero episode).
    t, nhtml = re.subn(r'</?(?:font|b|i|u|s)\b[^>\n]*>', '', t, flags=re.I)
    if nhtml:
        stats['html-tag'] += nhtml
    t, nads = strip_adverts(t)
    if nads:
        stats['advert'] += nads
    ratio = latin_ratio(t)
    out = []
    for c in t:
        if c in C1:                        # cp1252 byte that latin-1 turned into a control char
            c = C1[c]; stats['cp1252-c1'] += 1
        if c in DROP:
            stats['control'] += 1; continue
        if c in FOLD:
            stats['typography'] += 1; out.append(FOLD[c]); continue
        if ratio > 0.5 and c in HOMO:      # only in files that are basically Latin
            stats['homoglyph'] += 1; out.append(HOMO[c]); continue
        out.append(c)
    return ''.join(out), stats


def main():
    scan = sys.argv[1] == '--scan'
    files = (sorted(glob.glob(os.path.join(sys.argv[2], '**', '*.srt'), recursive=True))
             if scan else sys.argv[1:])
    for p in files:
        t, enc = read_any(p)
        new, st = clean(t)
        note = f"{enc:<12}"
        if st:
            note += "  " + " ".join(f"{k}:{v}" for k, v in sorted(st.items()))
        sus = collections.Counter(m.group(0) for m in SUSPECT.finditer(new))
        # a lone year or a track number is fine; only mixed letter/digit words are suspicious
        sus = {k: v for k, v in sus.items() if not k.isdigit()}
        if sus:
            note += "  SUSPECT:" + ",".join(f"{k}x{v}" for k, v in
                                            sorted(sus.items(), key=lambda x: -x[1])[:5])
        changed = (new != t) or enc not in ('utf-8', 'utf-8-sig')
        print(f"  {os.path.relpath(p):<30} {note}{'' if changed else '   (already clean)'}")
        if scan or not changed:
            continue
        if not os.path.exists(p + '.raw'):
            os.replace(p, p + '.raw')
        else:
            os.remove(p)
        open(p, 'w', encoding='utf-8').write(new)


if __name__ == '__main__':
    main()
