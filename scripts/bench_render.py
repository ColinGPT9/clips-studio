"""Render benchmark: the golden-render cases, timed on each encoder.

The cases of scripts/golden_renders.py (plain_vertical, captions_highlight,
cuts_music, landscape) go through the app's own clip renderer
(core/pipeline.py _render_files), once on the CPU encoder (libx264) and once
on NVENC when this PC has a working one; NVENC is reported as unavailable,
with FFmpeg's reason, when it has not. Each render is measured while it runs:

- wall time, and CPU time of this process and every process it starts
  (FFmpeg included);
- RAM: the peak working set of this process and its children together,
  sampled every --interval seconds (needs psutil; "not measured" without it);
- GPU memory and utilisation through nvidia-smi, when it is installed. These
  are for the whole card, other programs included: the baseline before each
  render is recorded beside the peak;
- the clip: size, bitrate, resolution, codec, and which encoder wrote its
  first frame.

    python scripts/bench_render.py --out RESULTS.json
    python scripts/bench_render.py --out RESULTS.json --source VIDEO.mp4 --start 600 --duration 30

The synthetic source is the golden renders' (testsrc2 with a tone, made with
the bundled FFmpeg), --synthetic-seconds long. --source renders a window of a
real video instead, [--start, --start + --duration], seeking into it as the
app does; the video is only read. The transcript is synthetic either way, so
no Whisper and no Ollama run, and nothing is downloaded.

Tracking runs where the app would run it (--tracking auto: on the GPU when
CUDA is usable), or on the CPU with --tracking cpu. Before the timed renders,
a short warm-up render loads the tracker and builds the end cards, which the
app builds once per format and keeps; --no-warmup times that too.

--out must be outside the repository. Renders happen in a scratch folder
(--work, default a new temporary folder), with the app's data folder pointed
inside it: no app code, no setting and none of the app's own data is touched.
The folder is deleted afterwards unless --keep-work.

FFmpeg is the one the app uses (core/binaries.py). To try another build, set
CLIPS_STUDIO_FFMPEG and CLIPS_STUDIO_FFPROBE, as for the app itself.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from scripts import golden_renders as golden
except ImportError:   # another package called "scripts" is ahead of this folder on the path
    import golden_renders as golden

MB = 1024 * 1024
SCHEMA = 1


# ---- CPU time --------------------------------------------------------------------------------


def _windows_job_clock():
    """CPU seconds of a Windows job object holding this process: every process
    it starts afterwards joins the job, and the job's totals keep the time of
    processes that have exited. None when the job cannot be set up."""
    import ctypes
    from ctypes import wintypes

    class Accounting(ctypes.Structure):   # JOBOBJECT_BASIC_ACCOUNTING_INFORMATION
        _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                    ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                    ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                    ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.AssignProcessToJobObject.restype = wintypes.BOOL
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.QueryInformationJobObject.restype = wintypes.BOOL
    k32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                              wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    job = k32.CreateJobObjectW(None, None)
    if not job or not k32.AssignProcessToJobObject(job, k32.GetCurrentProcess()):
        return None
    info = Accounting()

    def read() -> float:
        if not k32.QueryInformationJobObject(job, 1, ctypes.byref(info), ctypes.sizeof(info), None):
            raise OSError(ctypes.get_last_error(), "QueryInformationJobObject failed")
        return (info.TotalUserTime + info.TotalKernelTime) / 1e7   # 100 ns units

    try:
        read()
    except OSError:
        return None
    return read


class CpuClock:
    """CPU seconds used so far by this process and the processes it starts."""

    def __init__(self) -> None:
        self.read = time.process_time
        self.method = "this process only: child processes (FFmpeg) are NOT counted"
        if os.name == "nt":
            job = _windows_job_clock()
            if job is not None:
                self.read = job
                self.method = "Windows job object: this process and every process it starts, exited ones included"
            return
        try:
            import resource
        except ImportError:
            return

        def rusage() -> float:
            me, kids = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
            return me.ru_utime + me.ru_stime + kids.ru_utime + kids.ru_stime

        self.read = rusage
        self.method = "getrusage: this process and its child processes, counted when they exit"


# ---- RAM -------------------------------------------------------------------------------------


class RamSampler:
    """The working set (RSS) of this process and of every process under it,
    sampled on a thread of its own. Its own CPU time is kept with each sample,
    so it can be taken off the render's."""

    def __init__(self, interval: float, skip_pids: set[int]) -> None:
        import psutil

        self.psutil = psutil
        self.interval = interval
        self.skip = skip_pids
        self.samples: list[tuple[float, int, int, int, float]] = []   # t, self, tree, biggest child, own cpu
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="bench-ram", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        me = self.psutil.Process()
        while not self._stop.is_set():
            t = time.perf_counter()
            try:
                own = me.memory_info().rss
                kids = me.children(recursive=True)
            except self.psutil.Error:
                own, kids = 0, []
            tree, biggest = own, 0
            for kid in kids:
                if kid.pid in self.skip:
                    continue
                try:
                    rss = kid.memory_info().rss
                except self.psutil.Error:
                    continue
                tree += rss
                biggest = max(biggest, rss)
            self.samples.append((t, own, tree, biggest, time.thread_time()))
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def window(self, t0: float, t1: float) -> tuple[dict, float]:
        """The RAM figures between t0 and t1, and the sampler's CPU seconds then."""
        samples = list(self.samples)
        inside = [s for s in samples if t0 <= s[0] <= t1]
        before = [s for s in samples if s[0] < t0]
        if not inside:
            return {"measured": False, "why": "no sample fell inside the render"}, 0.0
        overhead = inside[-1][4] - (before[-1][4] if before else inside[0][4])
        return {
            "measured": True,
            "samples": len(inside),
            "peak_tree_mb": round(max(s[2] for s in inside) / MB),
            "peak_self_mb": round(max(s[1] for s in inside) / MB),
            "peak_child_mb": round(max(s[3] for s in inside) / MB),
            "self_before_mb": round(before[-1][1] / MB) if before else None,
        }, overhead


# ---- GPU -------------------------------------------------------------------------------------


def _number(text: str) -> float | None:
    try:
        return float(text.strip())
    except ValueError:
        return None   # "[N/A]", "[Not Supported]"


class GpuSampler:
    """nvidia-smi, looping every --interval, read on a thread. Started before
    the CPU clock's job exists, so its own CPU time is not the render's."""

    FIELDS = ("index", "memory.used", "utilization.gpu", "utilization.encoder", "utilization.decoder")

    def __init__(self, interval: float) -> None:
        self.available = False
        self.why = "nvidia-smi not found"
        self.info: list[dict] = []
        self.samples: list[tuple[float, dict[str, dict[str, float | None]]]] = []
        self.proc = None
        self.exe = shutil.which("nvidia-smi")
        if not self.exe:
            return
        self.fields = list(self.FIELDS)
        if self._query(self.fields) is None:
            self.fields = ["index", "memory.used", "utilization.gpu"]   # an older driver
            if self._query(self.fields) is None:
                self.why = "nvidia-smi did not answer"
                return
        for line in (self._query(["index", "name", "driver_version", "memory.total"]) or "").splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 4:
                self.info.append({"index": parts[0], "name": parts[1], "driver": parts[2],
                                  "memory_total_mb": _number(parts[3])})
        self.proc = subprocess.Popen(
            [self.exe, f"--query-gpu={','.join(self.fields)}", "--format=csv,noheader,nounits",
             "-lms", str(max(50, int(interval * 1000)))],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self._thread = threading.Thread(target=self._read, name="bench-gpu", daemon=True)
        self._thread.start()
        self.available = True
        self.why = ""

    def _query(self, fields: list[str]) -> str | None:
        try:
            r = subprocess.run([self.exe, f"--query-gpu={','.join(fields)}", "--format=csv,noheader,nounits"],
                               capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return None
        return r.stdout if r.returncode == 0 and r.stdout.strip() else None

    def _read(self) -> None:
        for line in self.proc.stdout:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) != len(self.fields):
                continue
            values = {f: _number(v) for f, v in zip(self.fields[1:], parts[1:])}
            self.samples.append((time.perf_counter(), {parts[0]: values}))

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc is not None else None

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def window(self, t0: float, t1: float) -> dict:
        if not self.available:
            return {"measured": False, "why": self.why}
        out: dict = {"measured": True, "scope": "the whole card, every program on it"}
        samples = list(self.samples)
        for gpu in sorted({k for _, s in samples for k in s}):
            series = [(t, s[gpu]) for t, s in samples if gpu in s]
            inside = [v for t, v in series if t0 <= t <= t1]
            before = [v for t, v in series if t < t0]
            if not inside:
                out[f"gpu{gpu}"] = {"samples": 0}
                continue

            def peak(field: str, values=inside) -> float | None:
                seen = [v[field] for v in values if v.get(field) is not None]
                return max(seen) if seen else None

            util = [v["utilization.gpu"] for v in inside if v.get("utilization.gpu") is not None]
            base = before[-1].get("memory.used") if before else None
            top = peak("memory.used")
            out[f"gpu{gpu}"] = {
                "samples": len(inside),
                "memory_before_mb": base,
                "memory_peak_mb": top,
                "memory_added_mb": (top - base) if top is not None and base is not None else None,
                "utilization_peak": peak("utilization.gpu"),
                "utilization_mean": round(statistics.fmean(util), 1) if util else None,
                "encoder_peak": peak("utilization.encoder"),
                "decoder_peak": peak("utilization.decoder"),
            }
        return out


# ---- the run ---------------------------------------------------------------------------------


def cpu_name() -> str:
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    return platform.processor() or platform.machine()


def machine(gpu: GpuSampler) -> dict:
    info = {"os": platform.platform(), "python": platform.python_version(), "cpu": cpu_name(),
            "logical_cpus": os.cpu_count()}
    try:
        import psutil

        info["physical_cores"] = psutil.cpu_count(logical=False)
        info["ram_gb"] = round(psutil.virtual_memory().total / 2**30, 1)
    except ImportError:
        pass
    if gpu.info:
        info["gpus"] = gpu.info
    return info


def probe_source(path: Path) -> dict:
    doc = golden.probe(path)
    video = next((s for s in doc["streams"] if s.get("codec_type") == "video"), None)
    audio = next((s for s in doc["streams"] if s.get("codec_type") == "audio"), None)
    if video is None:
        raise SystemExit(f"{path} has no video stream")
    duration = float(doc.get("duration") or video.get("duration") or 0)
    if duration <= 0:
        raise SystemExit(f"{path}: ffprobe gives no duration")
    return {"duration": duration, "width": video.get("width"), "height": video.get("height"),
            "codec": video.get("codec_name"), "pix_fmt": video.get("pix_fmt"),
            "frame_rate": video.get("r_frame_rate"),
            "audio": (f"{audio.get('codec_name')} {audio.get('sample_rate')} Hz {audio.get('channels')} ch"
                      if audio else None)}


def describe_output(path: Path) -> dict:
    doc = golden.probe(path)
    video = next((s for s in doc["streams"] if s.get("codec_type") == "video"), {})
    audio = next((s for s in doc["streams"] if s.get("codec_type") == "audio"), {})
    size = path.stat().st_size
    duration = float(doc.get("duration") or 0)
    return {
        "bytes": size,
        "duration_s": duration,
        "bitrate_kbps": round(size * 8 / duration / 1000) if duration else None,
        "video": {"codec": video.get("codec_name"), "profile": video.get("profile"),
                  "width": video.get("width"), "height": video.get("height"),
                  "pix_fmt": video.get("pix_fmt"), "frame_rate": video.get("avg_frame_rate"),
                  "bitrate_kbps": round(int(video["bit_rate"]) / 1000) if video.get("bit_rate") else None},
        "audio": {"codec": audio.get("codec_name"), "sample_rate": audio.get("sample_rate"),
                  "channels": audio.get("channels"),
                  "bitrate_kbps": round(int(audio["bit_rate"]) / 1000) if audio.get("bit_rate") else None},
        "first_frame_encoder": golden.video_encoder_of(path),
    }


def encoder_status(name: str) -> dict:
    """Whether the app's encoder `name` works here, as the app decides it
    (video/encoding.py): its probe encode with the exact render arguments."""
    from core.binaries import ffmpeg
    from video import encoding

    if name == "cpu":
        return {"available": True, "args": encoding.CPU_ARGS}
    candidates = getattr(encoding, "_CANDIDATES", {})
    if name not in candidates:
        # An unknown name would fall through to the app's "auto" choice.
        return {"available": False, "why": f"the app has no encoder called {name!r} "
                                           f"(it has cpu, {', '.join(candidates)})"}
    encoding.video_encoder_args({"video": {"encoder": name}})
    if encoding.using_hardware_encoder():
        return {"available": True, "args": encoding.video_encoder_args()}
    args = candidates[name]
    r = subprocess.run([ffmpeg(), "-v", "error", "-f", "lavfi", "-i", "color=black:s=256x256:d=0.1", *args,
                        "-f", "null", "-"], capture_output=True, text=True, timeout=60)
    lines = [re.sub(r"^\[[^\]]*\]\s*", "", ln).strip() for ln in (r.stderr or "").splitlines()]
    lines = [ln for ln in lines if ln and not ln.startswith("[")]
    return {"available": False, "args": args, "why": " ".join(lines[:2]) or f"its probe encode failed ({r.returncode})"}


def table(runs: list[dict]) -> list[str]:
    head = (f"{'case':<20} {'encoder':<7} {'n':>2} {'wall s':>7} {'cpu s':>7} {'RAM MB':>7} "
            f"{'VRAM +MB':>8} {'GPU %':>5} {'enc %':>5} {'size MB':>8} {'Mbit/s':>7}  {'frame':<10} codec")
    lines = [head, "-" * len(head)]
    groups: dict[tuple[str, str], list[dict]] = {}
    for run in runs:
        groups.setdefault((run["case"], run["encoder"]), []).append(run)
    for (case, enc), group in groups.items():
        ok = [r for r in group if r["ok"]]
        if not ok:
            lines.append(f"{case:<20} {enc:<7} {len(group):>2}  failed: {group[0].get('error', '?')[:80]}")
            continue

        def med(get, ok=ok):
            values = [v for v in (get(r) for r in ok) if v is not None]
            return statistics.median(values) if values else None

        def fmt(value, spec: str, width: int) -> str:
            return f"{value:{spec}}".rjust(width) if value is not None else "-".rjust(width)

        def gpu(run: dict, key: str):
            return (run["gpu"].get("gpu0") or {}).get(key) if run["gpu"].get("measured") else None

        last = ok[-1]["output"]
        frame = f"{last['video']['width']}x{last['video']['height']}"
        codec = f"{last['video']['codec']} {last['video']['profile'] or ''}".strip()
        if enc != "cpu" and last["first_frame_encoder"] == "libx264":
            codec += " (written by libx264!)"
        if len(ok) < len(group):
            codec += f" ({len(group) - len(ok)} failed)"
        lines.append(
            f"{case:<20} {enc:<7} {len(ok):>2} {fmt(med(lambda r: r['wall_s']), '.1f', 7)} "
            f"{fmt(med(lambda r: r['cpu_s']), '.1f', 7)} "
            f"{fmt(med(lambda r: r['ram'].get('peak_tree_mb')), '.0f', 7)} "
            f"{fmt(med(lambda r: gpu(r, 'memory_added_mb')), '+.0f', 8)} "
            f"{fmt(med(lambda r: gpu(r, 'utilization_peak')), '.0f', 5)} "
            f"{fmt(med(lambda r: gpu(r, 'encoder_peak')), '.0f', 5)} "
            f"{fmt(med(lambda r: r['output']['bytes'] / MB), '.2f', 8)} "
            f"{fmt(med(lambda r: (r['output']['bitrate_kbps'] or 0) / 1000), '.2f', 7)}  {frame:<10} {codec}")
    return lines


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    parser = argparse.ArgumentParser(
        description="Time the golden-render cases through the app's clip renderer, per encoder.",
        epilog="cases:\n" + "\n".join(f"  {c.name:<20} {c.about}" for c in golden.CASES),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True, help="the JSON results file (outside the repository)")
    parser.add_argument("--source", type=Path, help="a real video to render from (only read); default: synthetic")
    parser.add_argument("--start", type=float, help="where the clip starts in the source, seconds")
    parser.add_argument("--duration", type=float, help="how long the clip is, seconds")
    parser.add_argument("--synthetic-seconds", type=float, default=golden.SOURCE_SECONDS,
                        help="length of the synthetic source (default %(default)s)")
    parser.add_argument("--encoders", default="cpu,nvenc", help="comma-separated: cpu, nvenc, amf, qsv "
                        "(default %(default)s)")
    parser.add_argument("--cases", help="comma-separated case names (default: all)")
    parser.add_argument("--tracking", choices=("auto", "cpu"), default="auto",
                        help="auto: where the app would track (the GPU when CUDA is usable); cpu: forced")
    parser.add_argument("--repeat", type=int, default=1, help="renders per case and encoder (default 1)")
    parser.add_argument("--interval", type=float, default=0.1, help="sampling interval, seconds (default 0.1)")
    parser.add_argument("--no-warmup", action="store_true", help="time the first render's model load and end cards too")
    parser.add_argument("--work", type=Path, help="scratch folder (default: a new temporary folder)")
    parser.add_argument("--keep-work", action="store_true", help="keep the scratch folder and its clips")
    args = parser.parse_args(argv)

    out = golden.outside_repo(args.out, "--out")
    if out.is_dir():
        parser.error(f"--out {out} is a folder: name the JSON file to write")
    cases = golden.pick_cases(args.cases)
    encoders = [e.strip() for e in args.encoders.split(",") if e.strip()]
    if not encoders or args.repeat < 1 or args.interval <= 0:
        parser.error("need at least one encoder, --repeat >= 1 and --interval > 0")
    source = args.source.expanduser().resolve() if args.source else None
    if source is not None and not source.is_file():
        parser.error(f"--source {source} is not a file")
    work = golden.outside_repo(Path(args.work) if args.work else Path(tempfile.mkdtemp(prefix="clips-bench-")),
                               "--work")
    if work.exists() and any(work.iterdir()) and not (work / golden.MARKER).exists():
        parser.error(f"--work {work} is not empty: pick an empty or new folder")
    work = golden.fresh_folder(work)   # an earlier bench's own scratch is replaced

    # Order matters: nvidia-smi starts before the job object exists, so its
    # CPU time is never counted as the render's.
    gpu = GpuSampler(args.interval)
    clock = CpuClock()
    try:
        ram: RamSampler | None = RamSampler(args.interval, {gpu.pid} if gpu.pid else set())
        ram_how = f"psutil: working set of this process and every process under it, every {args.interval:g} s"
    except ImportError:
        ram, ram_how = None, "not measured (psutil is not installed)"

    try:
        return bench(args, out, cases, encoders, source, work, gpu, clock, ram, ram_how)
    finally:
        if ram is not None:
            ram.stop()
        gpu.stop()
        if args.keep_work:
            print(f"Scratch folder kept: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


def bench(args, out: Path, cases: list, encoders: list[str], source: Path | None, work: Path,
          gpu: GpuSampler, clock: CpuClock, ram: RamSampler | None, ram_how: str) -> int:
    from core import gpu as app_gpu
    from video.encoding import using_hardware_encoder, video_encoder_args

    config = golden.app_config(work / "data", encoder="cpu")
    golden.require_local_weights(config)
    if args.tracking == "cpu":
        golden.force_cpu_tracking("CPU tracking forced by scripts/bench_render.py --tracking cpu")
    workers = golden.share_the_cpu(config)

    inputs = work / "inputs"
    inputs.mkdir()
    music = golden.make_music(inputs / "music.wav")
    if source is None:
        seconds = float(args.synthetic_seconds)
        if seconds < 3:
            raise SystemExit("--synthetic-seconds must be at least 3")
        clip_source = golden.make_source(inputs / "source.mp4", seconds)
        start = 1.0 if args.start is None else float(args.start)
        end = (seconds - 1.0) if args.duration is None else start + float(args.duration)
        end = min(end, seconds)
        source_info = {"kind": "synthetic", "seconds": seconds, "ffmpeg_args": golden.source_args(seconds)}
    else:
        clip_source = source
        info = probe_source(source)
        start = 0.0 if args.start is None else float(args.start)
        end = min(info["duration"], start + (30.0 if args.duration is None else float(args.duration)))
        source_info = {"kind": "file", "path": str(source), **info}
        if info["codec"] in ("av1", "vp9", "hevc"):
            print(f"Note: the source is {info['codec']}. The app converts such sources to H.264 before "
                  f"rendering; this bench renders from it as it is, so decoding costs more than in the app.")
    if start < 0 or end - start < 2.0:
        raise SystemExit(f"the clip window {start:g}-{end:g} s is too short (2 s at least) or outside the source")
    window = (round(start, 3), round(end, 3))
    segments = golden.synthetic_transcript(max(0.0, start - 2.0), end + 2.0)

    statuses = {name: encoder_status(name) for name in encoders}
    for name, status in statuses.items():
        if not status["available"]:
            print(f"Encoder {name}: unavailable: {status['why']}")

    warmup: dict = {"skipped": True}
    if not args.no_warmup:
        # The tracker's model and the end card of each format, as a running app
        # has them after its first clip.
        config["video"]["encoder"] = "cpu"
        video_encoder_args(config)
        warm_cases = [c for c in cases if c.name != "landscape"][:1] + [c for c in cases if c.name == "landscape"]
        began = time.perf_counter()
        for case in warm_cases:
            golden.render_case(case, clip_source, music, (window[0], min(window[1], window[0] + 2.0)), segments,
                               work / "warmup" / case.name, config)
        warmup = {"skipped": False, "cases": [c.name for c in warm_cases],
                  "seconds": round(time.perf_counter() - began, 1)}
        print(f"Warm-up ({', '.join(warmup['cases'])}): {warmup['seconds']} s")

    runs = []
    for enc in encoders:
        if not statuses[enc]["available"]:
            continue
        config["video"]["encoder"] = enc
        video_encoder_args(config)
        if (enc != "cpu") != using_hardware_encoder():
            statuses[enc] = {**statuses[enc], "available": False, "why": "its probe failed when its renders were due"}
            print(f"Encoder {enc}: {statuses[enc]['why']}; skipped")
            continue
        for case in cases:
            for n in range(args.repeat):
                clip_dir = work / "runs" / enc / f"{case.name}-{n + 1}"
                print(f"{case.name} on {enc} ({n + 1}/{args.repeat})...", flush=True)
                run = {"case": case.name, "encoder": enc, "repeat": n + 1, "ok": False}
                t0, c0 = time.perf_counter(), clock.read()
                try:
                    final, _opts = golden.render_case(case, clip_source, music, window, segments, clip_dir, config)
                except Exception as e:  # a failed render is a result too
                    t1, c1 = time.perf_counter(), clock.read()
                    run["error"] = " ".join(str(e).split())[-300:]
                    final = None
                else:
                    t1, c1 = time.perf_counter(), clock.read()
                ram_stats, overhead = ram.window(t0, t1) if ram is not None else ({"measured": False,
                                                                                    "why": ram_how}, 0.0)
                run.update({"wall_s": round(t1 - t0, 2), "cpu_s": round(max(0.0, c1 - c0 - overhead), 2),
                            "sampler_cpu_s": round(overhead, 2), "ram": ram_stats, "gpu": gpu.window(t0, t1)})
                if final is not None:
                    run["ok"] = True
                    run["output"] = describe_output(final)
                runs.append(run)

    tracking_device = app_gpu.torch_device()
    doc = {
        "schema": SCHEMA,
        "tool": "scripts/bench_render.py",
        "made_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **golden.git_commit(),
        "machine": machine(gpu),
        "ffmpeg": golden.ffmpeg_version(),
        "settings": golden.render_settings(config),
        "render_workers_assumed": workers,
        # cuda_initialised: whether torch set CUDA up at all in this process.
        # FFmpeg's hardware decoding (-hwaccel auto) uses the GPU either way.
        "tracking": {"asked": args.tracking, "device": tracking_device,
                     "cuda_initialised": golden.torch_used_cuda()},
        "source": source_info,
        "window": list(window),
        "encoders": statuses,
        "measurement": {"cpu_time": clock.method, "ram": ram_how,
                        "gpu": (f"nvidia-smi every {args.interval:g} s; whole card, every program on it"
                                if gpu.available else f"not measured ({gpu.why})"),
                        "wall_time": "time.perf_counter around the _render_files call"},
        "warmup": warmup,
        "runs": runs,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")

    what = (f"synthetic source ({source_info['seconds']:g} s)" if source is None
            else f"{source.name} ({source_info['width']}x{source_info['height']} {source_info['codec']})")
    print()
    print(f"Render bench: {what}, clip {window[0]:g}-{window[1]:g} s, tracking on {tracking_device}, "
          f"{doc['ffmpeg']['version']}")
    for name, status in statuses.items():
        if not status["available"]:
            print(f"  {name}: unavailable: {status['why']}")
    print(f"  CPU time: {clock.method}")
    print(f"  RAM: {ram_how}")
    print(f"  GPU: {doc['measurement']['gpu']}")
    print()
    for line in table(runs):
        print(line)
    print(f"\nResults: {out}")
    return 0 if runs and all(r["ok"] for r in runs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
