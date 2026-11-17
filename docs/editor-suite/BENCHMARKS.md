# Golden renders and render benchmarks

Two scripts look after plain auto-clipping while the editor suite is built. `scripts/golden_renders.py` proves a change leaves the rendered clips exactly as they were. `scripts/bench_render.py` measures what rendering costs: time, CPU, memory, GPU and file size.

Both send clips through the app's own renderer, `_render_files` in `core/pipeline.py`, which every auto-clipping job uses. Neither changes app code or settings, and neither touches the app's data folder: each points the data folder at a scratch folder of its own. Both refuse an output folder inside the repository.

They need the full install (numpy, OpenCV, torch, ultralytics), FFmpeg where the app finds it (`core/binaries.py`) and the tracker's weights (`yolov8n-pose.pt`) beside the code. They need no Ollama and no network. Golden renders need no GPU; the benchmark uses one when it is there.

## Golden renders

Record on `main`, then check a branch against that record on the same PC:

```
python scripts/golden_renders.py --record ../golden-renders     # on main
python scripts/golden_renders.py --check ../golden-renders      # on the branch
```

`--check` prints `identical` or `DIFFERENT` for each case, with what differs, and exits 1 when any case differs. A run renders four short clips and builds two end cards. `--only plain_vertical,landscape` runs some of the cases. `--record` refuses a folder that already holds a record unless you pass `--force`.

### What is fixed

- **The source** is made by the script with FFmpeg: 10 seconds of the `testsrc2` test pattern (1920x1080, 30 fps) with a 440 Hz tone, and a 220 Hz tone as the music. No footage is needed.
- **The transcript** is made-up words with fixed timings, so captions burn without Whisper.
- **The encoder** is the CPU one (`video.encoder: cpu`, libx264). NVENC output changes from one driver version to the next, so it is never part of a golden render.
- **Tracking runs on the CPU**, through the same switch the app uses on a PC without a usable GPU. The script stops if torch sets CUDA up anyway.
- **Everything else** is `config/settings.yaml` as it is in the checkout. The settings a render reads are saved in the record, and `--check` says when they differ.

### The cases

| Case | What it renders |
|---|---|
| `plain_vertical` | The shipped settings with nothing chosen: tracked 9:16, default captions, the end card |
| `captions_highlight` | As plain, with the word being said lit up |
| `cuts_music` | As plain, with the middle cut out (two keep ranges) and looped music under the voice |
| `landscape` | The 16:9 Longform profile: 1920x1080, captions, the end card |

The music in `cuts_music` is not ducked. When it is, the mix changes from run to run: `sidechaincompress` in `video_editor/export.py` stops at the first end of either of its inputs, and FFmpeg's thread timing decides how much music has gone through it by then. The music then stops a fraction of a second before the clip ends, by a different amount each run. Ducking can join the case once that mix is reproducible.

### What is compared

For each case:

- the clip's sha256;
- FFmpeg's `framemd5`: a checksum of every decoded video frame and audio frame, with its timestamps;
- ffprobe's streams and the clip's duration;
- the render options the app saves with the clip.

A difference reads like this (an example, shortened):

```
cuts_music           DIFFERENT
    file sha256: 3f6a0c2b9e41d857... -> 9c1d4e7a20b36f15... (2,903,059 -> 2,903,083 bytes)
    framemd5: stream 1 (audio): 883 -> 883 frames, 28 differ; first at frame 583 (6.305 s)
    ffprobe: streams[1].bit_rate: '109157' -> '109177'
```

A changed sha256 with an identical `framemd5` means the file changed but not the picture or the sound (the container, for example).

### The folder

```
golden.json          the record: commit, FFmpeg version, settings, inputs, each case's fingerprint
inputs/              the source and the music, rendered from by every check
record/<case>/       the recorded clip, framemd5.txt and ffprobe.json
check/<case>/        the same for the latest check, and check/result.json
```

`--check` empties `check/` first. It also makes the inputs again and says whether they still come out byte for byte. If they don't, FFmpeg itself has probably changed, and the cases will most likely differ too.

### Limits

- The guarantee is the same files on the same PC with the same FFmpeg. A record from another PC, another FFmpeg build or another core count may differ without any change to the code. Record and check on one machine.
- After a change that is meant to alter the output, merge it and record again on `main` with `--force`.

## Render benchmarks

```
python scripts/bench_render.py --out ../bench/synthetic.json
python scripts/bench_render.py --out ../bench/vod.json --source VIDEO.mp4 --start 600 --duration 30
```

The script renders the four golden cases on each encoder in `--encoders` (default `cpu,nvenc`), prints a table and writes the JSON. It exits 1 when a render fails, and the error is kept in the JSON. With `--source`, it renders a window of a real video, seeking into it as the app does. The video is only read. Without `--source`, it renders the synthetic source, `--synthetic-seconds` long. The transcript is synthetic either way.

An encoder that doesn't work on the PC is listed as unavailable, with FFmpeg's reason, and its runs are skipped. The app decides this the same way, with a test encode. To try another FFmpeg build, set `CLIPS_STUDIO_FFMPEG` and `CLIPS_STUDIO_FFPROBE`, as you would for the app.

Options:

- `--tracking auto` (the default) tracks where the app would: on the GPU when CUDA is usable. `--tracking cpu` forces the CPU.
- `--repeat N` renders each case N times. The table shows the median, and the JSON keeps every run.
- `--cases` runs some of the cases.
- `--interval` sets how often memory and GPU are sampled (0.1 s by default).
- `--work DIR` sets the scratch folder (a new temporary folder by default). It is deleted afterwards unless you pass `--keep-work`.

Before timing, short warm-up renders load the tracker and build the end card for each format. A running app does this once and then keeps them. `--no-warmup` times all of that as part of the first renders.

### What is measured, per render

- **Wall time** around the render.
- **CPU time** of the script and every process it starts, FFmpeg included. On Windows this comes from a job object, which also counts processes that have exited. Elsewhere it comes from `getrusage`. The sampler's own CPU is taken off.
- **RAM**: the peak working set of the script and its child processes together, sampled with psutil. It is reported as not measured without psutil.
- **GPU**, from `nvidia-smi` when it is installed: memory before and at the peak, and peak and mean utilisation, including the encoder and the decoder. These numbers cover the whole card, other programs too. The app decodes with `-hwaccel auto`, so a CPU encode also uses the GPU's decoder.
- **The clip**: size, bitrate, resolution, codec and profile, and which encoder wrote its first frame. The end card is always made with libx264, so the first frame is the one that tells.

The JSON also records the machine, the FFmpeg build, the commit, the settings a render reads, the tracking device, and how each figure was measured.

### Reading the numbers

- Compare runs from the same PC, and say which PC in any write-up. This document holds no machine's results on purpose.
- The synthetic pattern compresses unlike real footage. Use `--source` with real footage for file sizes and bitrates (for example, #203's export sizes).
- Close other GPU programs before a run: the GPU figures cover the whole card.
