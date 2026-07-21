# Investigation notes

How the tooling here was arrived at.

## The official encoder's slow write is O(n²) inside the codec DLL

`MobiclipMulticoreEncoder.exe` is a thin GUI over plugin DLLs (each exports a single
`FFpluginInit`): FFmpeg for input/decode/scale, `MobiclipEncoder.dll` for the codec,
`AviOutput.dll` for muxing (via FFmpeg `av_interleaved_write_frame`).

Diagnosed with Process Monitor + Process Explorer on the hung write phase:

- Output is written in tiny sequential 4 KB `WriteFile` calls, minutes of wall-time
  apart, with disk at 0% and one CPU core pegged (~3% of a 32-thread machine = 100% of
  one core).
- The busy thread's stack is entirely inside **`MobiclipEncoder.dll`** — not the muxer,
  not FFmpeg, not a `Sleep`. It is a **single-threaded O(n²) finalization** pass that
  runs after the (fast, all-core) encode passes complete at 99%.
- The delay grows with file size (decelerating output), confirming O(n²).

Ruled out: antivirus (excluded, no change), disk (NVMe at 0%), RAM, and Windows timer
resolution (already 1 ms; the stalls are tens of ms). The DLL is closed-source, so the
algorithm can't be fixed directly — hence **split the input so `n` is small** and run
copies in parallel.

Can't be parallelized from outside: the finalization is a serial dependency chain in
compiled code. More cores/affinity/VMs can't speed a single sequential thread; only
reducing the work (shorter segments) helps. Multiple *instances* on segments do run in
parallel — the single-instance lock is a mutex in the GUI shell, not the codec DLL.

## mobipeg's ffmpeg can't stream-copy moflex audio

mobipeg (FFmpeg fork with a moflex muxer) is a tempting one-liner for the combine, but:

- Its moflex muxer round-trips **video** perfectly (stream-copy split→concat preserves
  every frame).
- It **truncates audio** whenever the video is stream-copied: a full-audio 45 s file
  came back as 11 s (remux) / 2.9 s (split+concat). Re-encoding the audio didn't help;
  the common factor is copied 3D video (2× eye rate) desyncing its audio interleave.
- Confirmed with our own bit-exact decoder, so it is real corruption, not a read-back
  artifact. mobipeg's *encode* path produces full audio (that's how good test files were
  made) — only stream-copy is broken.

Conclusion: combine at the container-block level instead (see `moflex-format.md`), which
copies audio bytes untouched.

## Validation approach

The MobiClip decoder in the 3DS-player project is verified bit-exact vs FFmpeg, so it is
the reference. `pc_verify/test_audio` decodes a file's audio to PCM; comparing the PCM of
an original vs a combined file proves the audio survived exactly. Video frame counts are
checked with mobipeg's ffmpeg (its demux/decode of video is reliable; only its *mux* of
audio is not).
