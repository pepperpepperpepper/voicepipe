# Bundled audio — license + provenance

All ogg files in this directory were generated locally with ffmpeg's
`sine` source filter + `afade` envelope. They are pure synthesis (no
samples from elsewhere), authored by the voicepipe project, and
released under **CC0 1.0 Universal** — to the extent possible under
law, the authors waive all copyright and related rights. See
<https://creativecommons.org/publicdomain/zero/1.0/>.

| File | Duration | Character | Recipe |
|---|---|---|---|
| `success.ogg` | 270 ms | Ascending two-tone (E5 → A5) | sine 659.25 Hz / 130 ms + sine 880 Hz / 140 ms, concatenated, 10 ms attack + ~25 ms release on each tone |
| `error.ogg` | 330 ms | Descending two-tone (G4 → C4) | sine 392 Hz / 160 ms + sine 261.63 Hz / 170 ms, concatenated, 10 ms attack + ~35 ms release on each tone |
| `match.ogg` | 100 ms | Single bright blip (C6) | sine 1046.5 Hz / 100 ms, 5 ms attack + 30 ms release |

All three are mono, 44.1 kHz, libvorbis `-q:a 3`. The exact ffmpeg
invocations are reproducible from the commit that introduced these
files — see git log for
`android/app/src/main/res/raw/{success,error,match}.ogg`.
