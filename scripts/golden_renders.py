"""Golden renders: what plain auto-clipping writes, pinned down file by file.

The renderer every auto-clipping job uses (core/pipeline.py _render_files)
renders a few clips from a source this script makes itself, and what comes
out is recorded, so a run on another branch can be compared with it:

    python scripts/golden_renders.py --record DIR   # on main: render the cases, keep their fingerprints
    python scripts/golden_renders.py --check DIR    # on a branch: render again and compare;
                                                    # exit 1 on any difference

DIR must be outside the repository. --record writes DIR/golden.json, the
inputs (DIR/inputs/) and each case's clip, framemd5 and ffprobe output
(DIR/record/<case>/). --check renders into DIR/check/, which it empties
first, and leaves its files there to be looked at.

Fixed, so that the same code makes the same files:

- the source: 10 seconds of FFmpeg's testsrc2 (1920x1080, 30 fps) with a
  440 Hz sine tone, and a 220 Hz tone as the music, made with the bundled
  FFmpeg, so no footage is needed;
- the transcript: fixed words with fixed timings, so captions burn without
  Whisper;
- the encoder: video.encoder = cpu (libx264). NVENC output is not the same
  from one driver version to the next;
- tracking on the CPU (YOLO, and TalkNet when it runs), by the switch the
  app itself uses on a PC without a usable GPU (core/gpu.py).

Everything else is config/settings.yaml as it is in this checkout, with the
data folder pointed inside DIR: the end card is built there, and nothing of
the app's own data is read or written.

The cases (CASES below): plain_vertical, the shipped settings with nothing
chosen (tracked 9:16, default captions, the end card); captions_highlight;
cuts_music (its music is not ducked: the ducked mix differs from run to run,
see _cuts_music); landscape (the 16:9 Longform profile).

A case's fingerprint: the clip's sha256, FFmpeg's framemd5 (a checksum of
every decoded video frame and audio frame, with its timestamps), ffprobe's
streams and duration, and the render options the app would save with the
clip.

Needs the full install (numpy, OpenCV, torch, ultralytics and the YOLO
weights beside the code) and FFmpeg. No Ollama, no GPU and no network.
scripts/bench_render.py times these same cases.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import zip_longest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCHEMA = 1
SOURCE_SECONDS = 10.0
SOURCE_FPS = 30
WINDOW = (1.0, 9.0)          # the clip's window in the source, seconds
MARKER = ".golden-renders"   # left in every folder this script makes: the only folders it ever empties
CPU_TRACKING = "CPU tracking forced by scripts/golden_renders.py"

# The words the captions say. Made up, and said by nobody.
SENTENCES = (
    "Golden renders keep plain clips honest.",
    "Every word here has its own timing, so captions burn without Whisper.",
    "When a single frame changes, the check says where.",
)


# ---- the cases -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    name: str
    about: str
    # (music file, clip seconds) -> the clip's render options, as the editor or a job saves them
    opts: Callable[[Path, float], dict | None]


def _plain(_music: Path, _seconds: float) -> dict | None:
    return None


def _highlight(_music: Path, _seconds: float) -> dict | None:
    return {"caption_style": {"highlight": True}}


def _cuts_music(music: Path, seconds: float) -> dict | None:
    # Two kept stretches, so the middle is cut out, and music under the voice,
    # looped (it is shorter than the clip).
    #
    # Not ducked ("duck": False). The ducked mix is not the same from one run
    # to the next: sidechaincompress (video_editor/export.py) stops when the
    # first of its two inputs ends, and how much music has been through it by
    # then depends on FFmpeg's thread timing. Measured with this source, the
    # ducked music ended 0 to 0.26 s short of the clip, differently on 5 of 8
    # runs, so the last few hundred milliseconds of audio differed. Turn
    # ducking on here once that mix is made reproducible.
    return {"edit": {
        "keep": [[0.0, round(seconds * 0.325, 2)], [round(seconds * 0.5, 2), round(seconds, 2)]],
        "music": {"path": str(music), "volume": 0.3, "duck": False},
    }}


def _landscape(_music: Path, _seconds: float) -> dict | None:
    return {"profile": "short_clips"}


CASES: tuple[Case, ...] = (
    Case("plain_vertical", "shipped settings, nothing chosen: tracked 9:16, default captions, end card", _plain),
    Case("captions_highlight", "as plain, with the word being said lit up (caption_style.highlight)", _highlight),
    Case("cuts_music", "as plain, with two keep ranges (the middle cut out) and music, not ducked", _cuts_music),
    Case("landscape", "the 16:9 Longform profile (short_clips): 1920x1080, captions, end card", _landscape),
)


def pick_cases(only: str | None) -> list[Case]:
    if not only:
        return list(CASES)
    names = [n.strip() for n in only.split(",") if n.strip()]
    known = {c.name: c for c in CASES}
    unknown = [n for n in names if n not in known]
    if unknown:
        raise SystemExit(f"unknown case(s): {', '.join(unknown)} (known: {', '.join(known)})")
    return [known[n] for n in names]


# ---- inputs --------------------------------------------------------------------------------


def source_args(seconds: float = SOURCE_SECONDS) -> list[str]:
    """FFmpeg arguments for the test source: testsrc2 with a beeping 440 Hz
    tone, H.264 and AAC at 48 kHz like a download."""
    return [
        "-f", "lavfi", "-i", f"testsrc2=size=1920x1080:rate={SOURCE_FPS}",
        "-f", "lavfi", "-i", "sine=frequency=440:beep_factor=4:sample_rate=48000",
        "-t", f"{seconds:g}", "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-g", "60",
        "-c:a", "aac", "-b:a", "160k", "-ac", "2",
        "-fflags", "+bitexact", "-movflags", "+faststart",
    ]


MUSIC_ARGS = [
    "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000", "-t", "6", "-ac", "2",
    "-c:a", "pcm_s16le", "-fflags", "+bitexact",
]


def _ffmpeg_make(args: list[str], dst: Path) -> Path:
    from core.binaries import ffmpeg

    dst.parent.mkdir(parents=True, exist_ok=True)
    run([ffmpeg(), "-v", "error", "-nostdin", "-y", *args, str(dst)])
    return dst


def make_source(dst: Path, seconds: float = SOURCE_SECONDS) -> Path:
    return _ffmpeg_make(source_args(seconds), dst)


def make_music(dst: Path) -> Path:
    return _ffmpeg_make(MUSIC_ARGS, dst)


def make_inputs(folder: Path) -> dict[str, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    return {"source": make_source(folder / "source.mp4"), "music": make_music(folder / "music.wav")}


def synthetic_transcript(start: float, end: float) -> list:
    """Segments with word timings, as faster-whisper's are saved
    (transcription/transcriber.py), from `start` to `end` in source seconds:
    SENTENCES over and over, each word as long as it is wide."""
    from core.models import Segment

    segments = []
    t = start + 0.4
    i = 0
    while True:
        tokens = SENTENCES[i % len(SENTENCES)].split()
        words = []
        for token in tokens:
            length = min(0.40, max(0.15, 0.12 + 0.025 * len(token)))
            if t + length > end - 0.2:
                break
            words.append({"start": round(t, 2), "end": round(t + length, 2), "word": token})
            t += length + 0.05
        if words:
            segments.append(Segment(start=words[0]["start"], end=words[-1]["end"],
                                    text=" ".join(w["word"] for w in words), words=words))
        if len(words) < len(tokens):
            return segments
        t += 0.25
        i += 1


# ---- the app, set up as a job would be -----------------------------------------------------


def app_config(data_dir: Path, encoder: str = "cpu") -> dict:
    """config/settings.yaml as main.load_config reads it, without the
    environment's overrides, the data folder at `data_dir` and the encoder
    forced."""
    import yaml

    with open(ROOT / "config" / "settings.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    for section in ("clips", "tracking", "video", "paths"):
        config.setdefault(section, {})
    config["paths"]["data_dir"] = str(data_dir)
    config["video"]["encoder"] = encoder
    return config


def render_settings(config: dict) -> dict:
    """The config sections a render reads, as remote rendering sends them."""
    from remote_render.protocol import CONFIG_SECTIONS

    return {k: json.loads(json.dumps(config.get(k) or {})) for k in CONFIG_SECTIONS}


def require_local_weights(config: dict) -> None:
    """Refuse to start rather than let ultralytics download the detector."""
    from core.binaries import yolo_weights

    name = (config.get("tracking") or {}).get("detector") or "yolov8n-pose.pt"
    if not Path(yolo_weights(name)).is_file():
        raise SystemExit(f"The tracker's weights ({name}) are not beside the code, and ultralytics "
                         f"would download them. Copy {name} into {ROOT} first.")


def force_cpu_tracking(reason: str = CPU_TRACKING) -> None:
    """Tracking on the CPU, by the app's own switch: core/gpu.py answers "no
    usable GPU", as it does on a PC without one. The YOLO model then stays on
    the CPU (video/tracker.py _get_model) and every inference is given the
    CPU (torch_device()). FFmpeg is untouched: the encoder is chosen apart."""
    from core import gpu

    def no_gpu() -> tuple[bool, str]:
        return False, reason

    gpu.cuda_usable = no_gpu
    gpu._DEVICE = "cpu"
    if gpu.torch_device() != "cpu" or gpu.cuda_usable()[0]:
        raise SystemExit("Could not force CPU tracking: core/gpu.py has changed; update force_cpu_tracking()")


def torch_used_cuda() -> bool:
    """Whether torch has set CUDA up in this process: anything run on the GPU."""
    torch = sys.modules.get("torch")
    return bool(torch is not None and torch.cuda.is_initialized())


def share_the_cpu(config: dict) -> int:
    """Thread limits for OpenCV and torch, set once as an auto-clipping job
    sets them before its renders (core/pipeline.py process_video)."""
    from core.pipeline import _share_the_cpu

    workers = max(1, int((config.get("video") or {}).get("parallel_renders", 2)))
    _share_the_cpu(workers)
    return workers


def render_case(case: Case, source: Path, music: Path, window: tuple[float, float], segments: list,
                clip_dir: Path, config: dict) -> tuple[Path, str]:
    """One clip through core/pipeline.py _render_files, as a job renders it.
    Returns the clip and the render options the app would save with it."""
    from core.models import ClipCandidate
    from core.pipeline import _render_files

    start, end = window
    candidate = ClipCandidate(start=start, end=end, score=80, hook="Golden render")
    return _render_files(source, candidate, segments, clip_dir, config, case.opts(music, end - start), "en")


# ---- fingerprints --------------------------------------------------------------------------


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, **kw)
    if r.returncode != 0:
        err = r.stderr if isinstance(r.stderr, str) else r.stderr.decode("utf-8", "replace")
        raise RuntimeError(f"{Path(cmd[0]).name} failed ({r.returncode}): {err.strip()[-1500:]}")
    return r


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def framemd5(path: Path) -> str:
    """FFmpeg's framemd5 of every stream: one line per decoded video frame and
    audio frame, with its timestamps, size and MD5. Bitexact, so the header
    leaves out FFmpeg's version and only the content is compared."""
    from core.binaries import ffmpeg

    out = run([ffmpeg(), "-v", "error", "-nostdin", "-i", str(path), "-map", "0", "-fflags", "+bitexact",
               "-f", "framemd5", "-"]).stdout
    return out.decode("utf-8", "replace").replace("\r\n", "\n")


def probe(path: Path) -> dict:
    """ffprobe's streams and the container's duration (not the file name, which moves)."""
    from core.binaries import ffprobe

    doc = json.loads(run([ffprobe(), "-v", "error", "-show_streams", "-show_format", "-of", "json",
                          str(path)], text=True, encoding="utf-8").stdout)
    return {"streams": doc.get("streams", []), "duration": (doc.get("format") or {}).get("duration")}


def video_encoder_of(path: Path) -> str:
    """"libx264" when the first video frame carries x264's settings message,
    which libx264 writes and the hardware encoders do not; else "hardware"."""
    from core.binaries import ffmpeg

    first = subprocess.run([ffmpeg(), "-v", "error", "-nostdin", "-i", str(path), "-map", "0:v:0", "-c", "copy",
                            "-frames:v", "1", "-f", "h264", "-"], capture_output=True).stdout
    if not first:
        return "unknown"
    return "libx264" if b"x264 - core" in first else "hardware"


def fingerprint(clip: Path, folder: Path) -> dict:
    """Write the clip's framemd5 and ffprobe output into `folder`; return the summary."""
    frames = framemd5(clip)
    (folder / "framemd5.txt").write_text(frames, encoding="utf-8", newline="\n")
    streams = probe(clip)
    (folder / "ffprobe.json").write_text(json.dumps(streams, indent=2, sort_keys=True) + "\n",
                                         encoding="utf-8", newline="\n")
    return {
        "sha256": sha256_file(clip),
        "bytes": clip.stat().st_size,
        "framemd5_sha256": sha256_text(frames),
        "ffprobe_sha256": sha256_text(json.dumps(streams, sort_keys=True)),
        "video_encoder": video_encoder_of(clip),
    }


def saved_opts(render_opts_json: str, inputs: Path) -> dict:
    """The render options the app saves with the clip, with the inputs folder
    written as <inputs> so a moved DIR still compares."""
    opts = json.loads(render_opts_json) if render_opts_json else {}
    prefixes = sorted({str(inputs), inputs.as_posix()}, key=len, reverse=True)

    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items()}
        if isinstance(value, list):
            return [clean(v) for v in value]
        if isinstance(value, str):
            for prefix in prefixes:
                if value.startswith(prefix):
                    return "<inputs>" + value[len(prefix):].replace("\\", "/")
        return value

    return clean(opts)


# ---- diffs ---------------------------------------------------------------------------------


def _flatten(value, path: str = "") -> dict:
    if isinstance(value, dict):
        out: dict = {}
        for key in sorted(value):
            out.update(_flatten(value[key], f"{path}.{key}" if path else str(key)))
        return out or {path: {}}
    if isinstance(value, list):
        out = {}
        for i, item in enumerate(value):
            out.update(_flatten(item, f"{path}[{i}]"))
        return out or {path: []}
    return {path: value}


def json_diff(want, got, limit: int = 12) -> list[str]:
    """Field-by-field differences, e.g. "streams[1].sample_rate: '96000' -> '48000'"."""
    a, b = _flatten(want), _flatten(got)
    lines = []
    for key in sorted(set(a) | set(b)):
        if key not in b:
            lines.append(f"{key}: {a[key]!r} -> (gone)")
        elif key not in a:
            lines.append(f"{key}: (new) {b[key]!r}")
        elif a[key] != b[key]:
            lines.append(f"{key}: {a[key]!r} -> {b[key]!r}")
    if len(lines) > limit:
        lines = [*lines[:limit], f"... and {len(lines) - limit} more"]
    return lines


def _parse_framemd5(text: str) -> tuple[list[str], dict[str, list[list[str]]], dict[str, str], dict[str, float]]:
    header: list[str] = []
    rows: dict[str, list[list[str]]] = {}
    kinds: dict[str, str] = {}
    timebase: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            header.append(line)
            key, _, value = line[1:].partition(":")
            name, _, stream = key.strip().rpartition(" ")
            if name == "media_type":
                kinds[stream] = value.strip()
            elif name == "tb":
                num, _, den = value.strip().partition("/")
                try:
                    timebase[stream] = float(num) / float(den)
                except (ValueError, ZeroDivisionError):
                    pass
            continue
        parts = [p.strip() for p in line.split(",")]
        rows.setdefault(parts[0], []).append(parts)
    return header, rows, kinds, timebase


def framemd5_diff(want: str, got: str) -> list[str]:
    """Per stream: how many frames, how many differ and where the first one is."""
    w_head, w_rows, kinds, tb = _parse_framemd5(want)
    g_head, g_rows, g_kinds, g_tb = _parse_framemd5(got)
    lines = [f"header: {a or '(none)'} -> {b or '(none)'}"
             for a, b in zip_longest(w_head, g_head) if a != b]
    for stream in sorted(set(w_rows) | set(g_rows)):
        a, b = w_rows.get(stream, []), g_rows.get(stream, [])
        differ = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        if not differ and len(a) == len(b):
            continue
        kind = kinds.get(stream) or g_kinds.get(stream) or "?"
        line = f"stream {stream} ({kind}): {len(a)} -> {len(b)} frames, {len(differ)} differ"
        first = differ[0] if differ else min(len(a), len(b))
        row = (a[first] if first < len(a) else b[first]) if max(len(a), len(b)) > first else None
        scale = tb.get(stream) or g_tb.get(stream)
        if row is not None and scale and len(row) > 2:
            try:
                line += f"; first at frame {first} ({int(row[2]) * scale:.3f} s)"
            except ValueError:
                line += f"; first at frame {first}"
        lines.append(line)
        if first < len(a) and first < len(b):
            lines.append(f"  was: {', '.join(a[first])}")
            lines.append(f"  now: {', '.join(b[first])}")
    return lines


def compare(want: dict, got: dict, want_dir: Path, got_dir: Path) -> list[str]:
    """What differs between the recorded case and this run's, as readable lines."""
    problems = []
    if want["sha256"] != got["sha256"]:
        problems.append(f"file sha256: {want['sha256'][:16]}... -> {got['sha256'][:16]}... "
                        f"({want['bytes']:,} -> {got['bytes']:,} bytes)")
    if want["framemd5_sha256"] != got["framemd5_sha256"]:
        diff = framemd5_diff((want_dir / "framemd5.txt").read_text(encoding="utf-8"),
                             (got_dir / "framemd5.txt").read_text(encoding="utf-8"))
        problems += [f"framemd5: {line}" for line in diff or ["differs"]]
    if want["ffprobe_sha256"] != got["ffprobe_sha256"]:
        diff = json_diff(json.loads((want_dir / "ffprobe.json").read_text(encoding="utf-8")),
                         json.loads((got_dir / "ffprobe.json").read_text(encoding="utf-8")))
        problems += [f"ffprobe: {line}" for line in diff or ["differs"]]
    if want.get("saved_opts") != got.get("saved_opts"):
        problems += [f"saved render options: {line}"
                     for line in json_diff(want.get("saved_opts"), got.get("saved_opts")) or ["differ"]]
    return problems


# ---- folders -------------------------------------------------------------------------------


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


def outside_repo(path: Path, what: str) -> Path:
    """`path` resolved, or SystemExit when it is in this repository or holds it."""
    resolved = Path(path).expanduser().resolve()
    root = ROOT.resolve()
    if _same(resolved, root) or any(_same(p, root) for p in resolved.parents):
        raise SystemExit(f"{what} must be outside the repository ({root}); got {resolved}")
    if any(_same(p, resolved) for p in root.parents):
        raise SystemExit(f"{what} must not be a folder that holds the repository ({root}); got {resolved}")
    return resolved


def remove_made(folder: Path) -> None:
    """Delete `folder`, but only one this script made (it holds MARKER)."""
    if folder.exists():
        if not (folder / MARKER).exists():
            raise SystemExit(f"{folder} exists and was not made by this script; move it away first")
        shutil.rmtree(folder)


def fresh_folder(folder: Path) -> Path:
    """`folder`, empty and marked as this script's: made, or one already empty,
    or an earlier one of this script's own, replaced."""
    if not (folder.is_dir() and not any(folder.iterdir())):
        remove_made(folder)
        folder.mkdir(parents=True)
    (folder / MARKER).write_text("made by scripts/golden_renders.py; emptied on the next run\n", encoding="utf-8")
    return folder


def ffmpeg_version() -> dict:
    from core.binaries import ffmpeg, ffprobe

    try:
        first = run([ffmpeg(), "-hide_banner", "-version"], text=True).stdout.splitlines()[0]
    except (OSError, RuntimeError, IndexError) as e:   # reported, not fatal
        first = f"unknown ({e})"
    return {"ffmpeg": ffmpeg(), "ffprobe": ffprobe(), "version": first}


def git_commit() -> dict:
    try:
        sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, timeout=30).stdout.strip()
        return {"commit": sha or "unknown", "uncommitted_changes": bool(dirty)}
    except (OSError, subprocess.SubprocessError):
        return {"commit": "unknown", "uncommitted_changes": None}


# ---- record and check ----------------------------------------------------------------------


def render_all(cases: list[Case], inputs: dict[str, Path], work: Path, base: Path) -> tuple[dict, dict]:
    """Render every case into work/<case>/ and fingerprint it. Returns the
    fingerprints and the render settings used. The end card is built in
    work/data, which is removed afterwards."""
    from video.encoding import using_hardware_encoder, video_encoder_args

    data_dir = work / "data"
    config = app_config(data_dir, encoder="cpu")
    require_local_weights(config)
    force_cpu_tracking()
    video_encoder_args(config)
    if using_hardware_encoder():
        raise SystemExit("The CPU encoder could not be forced (video/encoding.py changed?)")
    share_the_cpu(config)
    segments = synthetic_transcript(0.0, SOURCE_SECONDS)

    results = {}
    for case in cases:
        folder = work / case.name
        folder.mkdir(parents=True)
        started = time.monotonic()
        print(f"  {case.name}: rendering ({case.about})", flush=True)
        final, opts_json = render_case(case, inputs["source"], inputs["music"], WINDOW, segments, folder, config)
        result = fingerprint(final, folder)
        result.update({
            "about": case.about,
            "file": final.relative_to(base).as_posix(),
            "saved_opts": saved_opts(opts_json, inputs["source"].parent),
            "seconds": round(time.monotonic() - started, 1),
        })
        if result["video_encoder"] != "libx264":
            raise SystemExit(f"{case.name}: the clip was not encoded by libx264 ({result['video_encoder']})")
        print(f"  {case.name}: {result['bytes']:,} bytes, sha256 {result['sha256'][:16]}... "
              f"({result['seconds']} s)", flush=True)
        results[case.name] = result
    if torch_used_cuda():
        raise SystemExit("torch set CUDA up during the renders: tracking did not stay on the CPU")
    shutil.rmtree(data_dir, ignore_errors=True)
    return results, render_settings(config)


def record(target: Path, cases: list[Case], force: bool) -> int:
    target = outside_repo(target, "DIR")
    manifest = target / "golden.json"
    if manifest.exists() and not force:
        raise SystemExit(f"{manifest} exists: pass --force to record over it, or --check against it")
    if target.exists() and any(target.iterdir()) and not manifest.exists():
        raise SystemExit(f"{target} is not empty and holds no golden.json: pick an empty or new folder")
    target.mkdir(parents=True, exist_ok=True)
    if manifest.exists():
        for old in ("record", "inputs", "check"):
            remove_made(target / old)
        manifest.unlink()

    print(f"Golden renders: recording into {target}")
    inputs = make_inputs(fresh_folder(target / "inputs"))
    work = fresh_folder(target / "record")
    results, settings = render_all(cases, inputs, work, target)
    doc = {
        "schema": SCHEMA,
        "tool": "scripts/golden_renders.py",
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **git_commit(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "ffmpeg": ffmpeg_version(),
        "window": list(WINDOW),
        "settings": settings,
        "inputs": {
            "source": {"file": "inputs/source.mp4", "sha256": sha256_file(inputs["source"]),
                       "ffmpeg_args": source_args()},
            "music": {"file": "inputs/music.wav", "sha256": sha256_file(inputs["music"]),
                      "ffmpeg_args": MUSIC_ARGS},
        },
        "transcript": [{"start": s.start, "end": s.end, "text": s.text, "words": s.words}
                       for s in synthetic_transcript(0.0, SOURCE_SECONDS)],
        "cases": results,
    }
    manifest.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Recorded {len(results)} case(s) in {manifest}")
    return 0


def check(target: Path, cases: list[Case]) -> int:
    target = outside_repo(target, "DIR")
    manifest = target / "golden.json"
    if not manifest.exists():
        raise SystemExit(f"No golden.json in {target}: run --record first")
    golden = json.loads(manifest.read_text(encoding="utf-8"))
    if golden.get("schema") != SCHEMA:
        raise SystemExit(f"{manifest} is schema {golden.get('schema')}; this script reads {SCHEMA}. Record again.")
    recorded = golden["cases"]
    missing = [c.name for c in cases if c.name not in recorded]
    if missing:
        raise SystemExit(f"Not in the record: {', '.join(missing)} (recorded: {', '.join(recorded)})")

    inputs = {"source": target / golden["inputs"]["source"]["file"],
              "music": target / golden["inputs"]["music"]["file"]}
    for key, path in inputs.items():
        if not path.is_file() or sha256_file(path) != golden["inputs"][key]["sha256"]:
            raise SystemExit(f"The recorded {key} ({path}) is missing or has changed: record again")

    print(f"Golden renders: checking against {target} "
          f"(recorded {golden.get('recorded_at', '?')} at {golden.get('commit', '?')})")
    work = fresh_folder(target / "check")
    notes = []
    remade = make_inputs(work / "inputs")
    same_inputs = all(sha256_file(remade[k]) == golden["inputs"][k]["sha256"] for k in remade)
    if not same_inputs:
        notes.append("the inputs no longer regenerate byte for byte (FFmpeg changed?); "
                     "this check renders from the recorded ones")
    now_ffmpeg = ffmpeg_version()["version"]
    if now_ffmpeg != golden.get("ffmpeg", {}).get("version"):
        notes.append(f"FFmpeg differs: {golden.get('ffmpeg', {}).get('version')} -> {now_ffmpeg}")

    results, settings = render_all(cases, inputs, work, target)
    setting_changes = json_diff(golden.get("settings"), settings)
    if setting_changes:
        notes.append("the render settings differ from the record's (config/settings.yaml): "
                     + "; ".join(setting_changes))
    (work / "result.json").write_text(json.dumps({"checked_at": datetime.now(timezone.utc).isoformat(
        timespec="seconds"), **git_commit(), "settings": settings, "cases": results}, indent=2) + "\n",
        encoding="utf-8", newline="\n")

    print()
    differ = 0
    for case in cases:
        want, got = recorded[case.name], results[case.name]
        problems = compare(want, got, (target / want["file"]).parent, (target / got["file"]).parent)
        print(f"  {case.name:<20} {'DIFFERENT' if problems else 'identical'}")
        for line in problems:
            print(f"      {line}")
        differ += bool(problems)
    for note in notes:
        print(f"  note: {note}")
    print(f"\nInputs regenerated {'byte for byte' if same_inputs else 'DIFFERENTLY'}.")
    if differ:
        print(f"{differ} of {len(cases)} case(s) differ from the record. This run's files: {work}")
        return 1
    print(f"All {len(cases)} case(s) identical to the record.")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    parser = argparse.ArgumentParser(
        description="Render fixed cases through the app's clip renderer and record or check their fingerprints.",
        epilog="cases:\n" + "\n".join(f"  {c.name:<20} {c.about}" for c in CASES),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--record", type=Path, metavar="DIR", help="render the cases and record them in DIR")
    mode.add_argument("--check", type=Path, metavar="DIR", help="render again and compare with DIR's record")
    parser.add_argument("--only", metavar="CASES", help="comma-separated case names (default: all)")
    parser.add_argument("--force", action="store_true", help="with --record: replace the record already in DIR")
    args = parser.parse_args(argv)
    cases = pick_cases(args.only)
    if args.record is not None:
        return record(args.record, cases, args.force)
    return check(args.check, cases)


if __name__ == "__main__":
    raise SystemExit(main())
