"""Run render code with every process launch faked and recorded.

tests/test_render_argv_snapshots.py uses this to prove that the FFmpeg work a
render does stays exactly the same while other work goes on around it. Nothing
real runs while a RenderHarness is installed:

- subprocess.run, Popen, call, check_call and check_output are replaced on the
  subprocess module, and in any engine module that took its own reference to
  one, along with os.system and os.popen. FFmpeg and ffprobe are faked. Any
  other program fails the test on the spot.
- A faked FFmpeg writes the file its command names: a few bytes of text, a
  "media descriptor" with the picture's size, frame rate and length. The fake
  ffprobe and the fake cv2.VideoCapture read it back, so a stage that checks
  its output exists, or probes it, sees what it would after a real encode. A
  final write (video.encoding.final_movflags()) comes out the size of the
  harness's canvas; a staging file keeps the size of what it was cut from.
- A raw-video decode (video.encoding.sampled_frames) streams FRAMES black
  frames; the cropper's encode takes whatever is piped into it and records
  how many bytes that was.
- The encoder is fixed, CPU or NVENC, by setting video/encoding.py's
  process-wide _selected, which monkeypatch puts back afterwards.

Every recorded command is normalised so a snapshot reads the same on Windows
and on Linux: the binary becomes "ffmpeg" or "ffprobe", the test's folder
becomes <TMP>, registered files their own placeholder (<SRC>, <OUT>), and the
separators inside those paths "/". The escapes FFmpeg's own syntax uses
(`\\,` in a filter) are left as they are: they are the same everywhere. The
.ass files and concat listings a command reads are captured, as text, when
the command is recorded, because the render deletes them once it is done.

Failures are pytest.fail(): a BaseException, so the render code's own
`except Exception` fallbacks can't swallow them.

Each fake hands the call to the real function when its harness is no longer
the active one, so a module that bound a fake while a harness was installed
(a lazy `from video.tracker import ...`) can't carry it into later tests.
"""

from __future__ import annotations

import copy
import difflib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

ENCODERS = ("cpu", "nvenc")

# Black frames a fake decode streams, and a fake OpenCV capture holds. Small,
# because each one is a full picture's worth of bytes.
FRAMES = 3

MEDIA_MARK = "CLIPS-KITTY-FAKE-MEDIA "

# What a fake video is unless it says otherwise: a 1080p30 stream recording
# with AAC stereo sound.
DEFAULT_MEDIA = {
    "w": 1920,
    "h": 1080,
    "rate": "30/1",
    "time_base": "1/15360",
    "duration": 600.0,
    "frames": FRAMES,
    "codec": "h264",
    "pix_fmt": "yuv420p",
    "first_frame": 0.0,
    "audio": {"sample_rate": 48000, "channels": 2},
}

_SUBPROCESS_API = ("run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput")

# Characters a path's own name can hold here. Everything else ends a path that
# sits inside a longer string (a filter graph, a concat listing's line).
_NAME = r"[^\\/\s'\",;:=\[\]|]"
_SUBTITLES = re.compile(r"\b(?:subtitles|ass)=(?:filename=)?'?([^,;:'\[\]]+?\.ass)'?(?=$|[,;:'\[\]])")
_LISTED = re.compile(r"^file '((?:[^']|'\\'')*)'$")
_DRIVE_PATH = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]")
_POSIX_PATH = re.compile(r"(?:^|[\s'\"=])/" + _NAME + r"+")
_OUTSIDE = ("<HOME>", "<SYSTMP>")

_ACTIVE: RenderHarness | None = None   # the harness whose fakes are live


def _fail(message: str):
    pytest.fail(message, pytrace=False)


def _path_pattern(form: str) -> str:
    """A path as a regex that accepts either separator between its parts."""
    return r"[\\/]".join(re.escape(part) for part in re.split(r"[\\/]", form))


class _Rule:
    """One path, or folder of paths, and the placeholder it becomes."""

    def __init__(self, form: str, placeholder: str, folder: bool):
        flags = re.IGNORECASE if os.name == "nt" else 0
        body = _path_pattern(form)
        self.length = len(form)
        self.placeholder = placeholder
        self.folder = folder
        if folder:
            self.whole = re.compile(body + r"((?:[\\/].*)?)", flags | re.DOTALL)
            self.inner = re.compile(body + r"(?!" + _NAME + r")((?:[\\/]" + _NAME + r"*)*)", flags)
        else:
            self.whole = re.compile(body, flags)
            self.inner = re.compile(body + r"(?!" + _NAME + r")(?![\\/])", flags)

    def replace(self, match: re.Match) -> str:
        rest = match.group(1) if self.folder else ""
        return self.placeholder + (rest or "").replace("\\", "/")


class _Job:
    """What one faked process does: its exit code, what it prints, and the
    file it writes (written when the process finishes)."""

    def __init__(self, record: dict, returncode: int = 0, stdout: str = "", stderr: str = "",
                 output: Path | None = None, info: dict | None = None, stream: int = 0):
        self.record = record
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.output = output
        self.info = info
        self.stream = stream    # bytes a raw decode streams on stdout


class _Sink:
    """A process's stdin: takes everything written to it and counts it."""

    def __init__(self):
        self.count = 0
        self.closed = False

    def write(self, data) -> int:
        if self.closed:
            raise ValueError("write to closed file")
        size = memoryview(data).nbytes
        self.count += size
        return size

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _Source:
    """A process's stdout or stderr: `total` zero bytes, then the end."""

    def __init__(self, total: int = 0, on_end=None, text: bool = False):
        self.left = total
        self.on_end = on_end
        self.text = text
        self.closed = False

    def _empty(self):
        return "" if self.text else b""

    def read(self, size: int | None = -1):
        if self.left <= 0:
            if self.on_end is not None:
                self.on_end()
                self.on_end = None
            return self._empty()
        take = self.left if size is None or size < 0 else min(size, self.left)
        self.left -= take
        return ("\0" * take) if self.text else bytes(take)

    def readline(self, size: int | None = -1):
        return self.read(size)

    def __iter__(self):
        while True:
            chunk = self.read(65536)
            if not chunk:
                return
            yield chunk

    def close(self) -> None:
        self.closed = True


class _Capture:
    """cv2.VideoCapture over a fake media file: its size, frame rate and
    frame count, and FRAMES black frames."""

    def __init__(self, harness: RenderHarness, source):
        self._info = harness.info(Path(source)) if isinstance(source, (str, os.PathLike)) else None
        self._pos = 0

    def isOpened(self) -> bool:
        return self._info is not None

    def _frames(self) -> int:
        return int(self._info["frames"]) if self._info else 0

    def get(self, prop) -> float:
        import cv2

        if not self._info:
            return 0.0
        return {
            cv2.CAP_PROP_FPS: _fps(self._info["rate"]),
            cv2.CAP_PROP_FRAME_WIDTH: float(self._info["w"]),
            cv2.CAP_PROP_FRAME_HEIGHT: float(self._info["h"]),
            cv2.CAP_PROP_FRAME_COUNT: float(self._frames()),
            cv2.CAP_PROP_POS_FRAMES: float(self._pos),
        }.get(prop, 0.0)

    def set(self, prop, value) -> bool:
        import cv2

        if prop == cv2.CAP_PROP_POS_FRAMES:
            self._pos = max(0, int(value))
            return True
        return False

    def grab(self) -> bool:
        if self._pos >= self._frames():
            return False
        self._pos += 1
        return True

    def retrieve(self, *_args):
        import numpy as np

        return True, np.zeros((int(self._info["h"]), int(self._info["w"]), 3), np.uint8)

    def read(self, *_args):
        if not self.grab():
            return False, None
        return self.retrieve()

    def release(self) -> None:
        pass


def _fps(rate: str) -> float:
    num, _, den = str(rate).partition("/")
    return float(num) / float(den or 1)


def _after(argv: list[str], flag: str) -> str | None:
    return argv[argv.index(flag) + 1] if flag in argv[:-1] else None


class RenderHarness:
    """Installs the fakes on `monkeypatch` (undone with it) and records
    everything the render code starts, under `root`, for one encoder.

    canvas: the size a final write comes out at (1080x1920 for a Short,
    1920x1080 for Longform), which is what a probe of the finished clip says.
    """

    def __init__(self, monkeypatch, root: Path, encoder: str = "cpu", canvas: tuple[int, int] = (1080, 1920)):
        if encoder not in ENCODERS:
            raise ValueError(f"encoder must be one of {ENCODERS}")
        self.mp = monkeypatch
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.encoder = encoder
        self.canvas = (int(canvas[0]), int(canvas[1]))
        self.commands: list[dict] = []
        self.calls: list[dict] = []
        self.problems: list[str] = []
        self._step: str | None = None
        self._failing: dict[str, int] = {}
        self._rules: list[_Rule] = []
        self._folder(self.root, "<TMP>")
        self._folder(ROOT, "<REPO>")
        self._folder(Path.home(), "<HOME>")
        self._folder(Path(tempfile.gettempdir()), "<SYSTMP>")
        self._install()

    # ---- setting up a case -------------------------------------------------------------

    def media(self, path, **info) -> Path:
        """Write a fake media file (a descriptor) and return its path.
        Relative paths are under the harness's root."""
        path = self._under_root(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        merged = copy.deepcopy(DEFAULT_MEDIA)
        merged.update(info)
        path.write_text(MEDIA_MARK + json.dumps(merged, sort_keys=True), encoding="utf-8")
        return path

    def source(self, name: str = "source.mp4", **info) -> Path:
        """The video being clipped, shown as <SRC>."""
        path = self.media(name, **info)
        self.name(path, "<SRC>")
        return path

    def out(self, name: str = "out.mp4") -> Path:
        """The case's output file, shown as <OUT>."""
        path = self._under_root(name)
        self.name(path, "<OUT>")
        return path

    def write(self, path, text: str) -> Path:
        path = self._under_root(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def name(self, path, placeholder: str) -> None:
        """Show this exact file as `placeholder` in the recording."""
        self._add(Path(path), placeholder, folder=False)

    def step(self, label: str | None) -> None:
        """Label the commands from here on (a case that makes several calls)."""
        self._step = label

    def fail_next(self, tool: str = "ffmpeg", times: int = 1) -> None:
        """Make the next `times` runs of `tool` fail, as a refused encode does."""
        self._failing[tool] = self._failing.get(tool, 0) + times

    def fake(self, owner, name: str, result=None, label: str | None = None) -> None:
        """Replace owner.name with a stand-in that records how it was called
        and returns `result` (a copy of it, or what it returns if callable)."""
        label = label or f"{owner.__name__.rsplit('.', 1)[-1]}.{name}"
        original = getattr(owner, name)

        def stand_in(*args, **kwargs):
            if _ACTIVE is not self:
                return original(*args, **kwargs)
            call = {"args": list(args), **kwargs} if args else dict(kwargs)
            self.calls.append({label: self.plain(call)})
            return result(*args, **kwargs) if callable(result) else copy.deepcopy(result)

        self.mp.setattr(owner, name, stand_in)

    def refuse(self, owner, name: str, why: str) -> None:
        """Replace owner.name with one that fails the test: something that
        would load a model or read real footage."""
        original = getattr(owner, name)

        def stand_in(*args, **kwargs):
            if _ACTIVE is not self:
                return original(*args, **kwargs)
            self._problem(f"{owner.__name__}.{name} was called: {why}")

        self.mp.setattr(owner, name, stand_in)

    # ---- reading back -------------------------------------------------------------------

    def info(self, path: Path) -> dict | None:
        """A fake media file's descriptor, or None for anything else."""
        try:
            with open(path, "rb") as f:
                head = f.read(4096)
        except OSError:
            return None
        mark = MEDIA_MARK.encode()
        if not head.startswith(mark):
            return None
        return json.loads(head[len(mark):].decode("utf-8"))

    def norm(self, text: str) -> str:
        """`text` with every known path replaced by its placeholder."""
        for rule in self._rules:
            match = rule.whole.fullmatch(text)
            if match:
                return rule.replace(match)
        for rule in self._rules:
            text = rule.inner.sub(rule.replace, text)
        return text

    def plain(self, value):
        """A value as JSON with every path normalised."""
        if isinstance(value, os.PathLike):
            return self.norm(os.fspath(value))
        if isinstance(value, str):
            return self.norm(value)
        if isinstance(value, dict):
            return {str(k): self.plain(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.plain(v) for v in value]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return self.norm(repr(value))

    def files(self) -> list[str]:
        """Every file under the root, as root-relative POSIX paths."""
        return sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file())

    def result(self, **extra) -> dict:
        """The recording, ready for a snapshot. `extra` (a returned path, the
        render options JSON, an error) is normalised the same way."""
        self.check()
        out: dict = {"commands": self.commands}
        if self.calls:
            out["calls"] = self.calls
        for key, value in extra.items():
            if key == "render_opts" and isinstance(value, str):
                out[key] = self.plain(json.loads(value)) if value else ""
            else:
                out[key] = self.plain(value)
        self._leftovers(out, "the recording")
        out["files"] = self.files()
        self.check()
        return out

    def check(self) -> None:
        if self.problems:
            _fail("\n".join(self.problems))

    # ---- the fakes ----------------------------------------------------------------------

    def _install(self) -> None:
        from video import encoding

        mp = self.mp
        mp.setattr(sys.modules[__name__], "_ACTIVE", self)
        mp.setattr(encoding, "_selected", self.selection())
        harness = self
        originals = {name: getattr(subprocess, name) for name in _SUBPROCESS_API}

        class Popen(_FakePopen):
            def __new__(cls, *args, **kwargs):
                if _ACTIVE is not harness:
                    return originals["Popen"](*args, **kwargs)
                return super().__new__(cls)

            def __init__(self, args, *pos, **kwargs):
                super().__init__(harness, args, *pos, **kwargs)

        def live(name: str, fake):
            original = originals.get(name) or getattr(os, name)

            def call(*args, **kwargs):
                if _ACTIVE is not harness:
                    return original(*args, **kwargs)
                return fake(*args, **kwargs)

            return call

        fakes = {
            "run": live("run", self._run),
            "Popen": Popen,
            "call": live("call", self._call),
            "check_call": live("check_call", self._check_call),
            "check_output": live("check_output", self._check_output),
            "getoutput": live("getoutput", self._shell),
            "getstatusoutput": live("getstatusoutput", self._shell),
        }
        for name, fake in fakes.items():
            mp.setattr(subprocess, name, fake)
        # An engine module that did `from subprocess import run` holds its own
        # reference, which patching the module above would not reach.
        for module in list(sys.modules.values()):
            if not _in_repo(module):
                continue
            try:
                names = list(vars(module).items())
            except TypeError:
                continue
            for attr, value in names:
                for name, original in originals.items():
                    if value is original:
                        mp.setattr(module, attr, fakes[name])
        for name in ("system", "popen", "startfile"):
            if hasattr(os, name):
                mp.setattr(os, name, live(name, self._shell))

        try:
            import cv2
        except ImportError:
            cv2 = None
        if cv2 is not None:
            real_capture = cv2.VideoCapture

            class VideoCapture(_Capture):
                def __new__(cls, *args, **kwargs):
                    if _ACTIVE is not harness:
                        return real_capture(*args, **kwargs)
                    return super().__new__(cls)

                def __init__(self, source=None, *_args, **_kwargs):
                    super().__init__(harness, source)

            mp.setattr(cv2, "VideoCapture", VideoCapture)

        # A model never loads, whatever path the code takes to it.
        for module_name in ("video.tracker", "gaming.detect"):
            module = sys.modules.get(module_name)
            if module is not None and hasattr(module, "_get_model"):
                self.refuse(module, "_get_model", "a detection model would load")

    def selection(self) -> tuple[str, list[str]]:
        from video import encoding

        if self.encoder == "cpu":
            return "cpu", encoding.CPU_ARGS
        return self.encoder, encoding._CANDIDATES[self.encoder]

    def _problem(self, message: str):
        self.problems.append(message)
        _fail(message)

    def _shell(self, *args, **_kwargs):
        self._problem(f"a shell command would run: {args[:1]!r}")

    def _under_root(self, path) -> Path:
        path = Path(path)
        return path if path.is_absolute() else self.root / path

    def _folder(self, path: Path, placeholder: str) -> None:
        self._add(path, placeholder, folder=True)

    def _add(self, path: Path, placeholder: str, folder: bool) -> None:
        forms = {str(path), path.as_posix()}
        try:
            resolved = path.resolve()
            forms |= {str(resolved), resolved.as_posix()}
        except OSError:
            pass
        # As a concat listing writes a quote in it (video.encoding.concat_entry).
        forms |= {form.replace("'", "'\\''") for form in forms if "'" in form}
        known = {(r.whole.pattern, r.placeholder) for r in self._rules}
        for form in forms:
            rule = _Rule(form, placeholder, folder)
            if (rule.whole.pattern, placeholder) not in known:
                self._rules.append(rule)
                known.add((rule.whole.pattern, placeholder))
        # Longest first, so a file wins over its folder and a folder over its parent.
        self._rules.sort(key=lambda r: -r.length)

    def _argv(self, args) -> list[str]:
        if isinstance(args, (str, bytes)):
            self._problem(f"a shell-style command string would run: {args!r}")
        return [os.fspath(a) if isinstance(a, os.PathLike) else str(a) for a in args]

    def _tool(self, exe: str) -> str:
        from core import binaries

        if exe == binaries.ffmpeg():
            return "ffmpeg"
        if exe == binaries.ffprobe():
            return "ffprobe"
        name = Path(exe).name.lower()
        name = name[:-4] if name.endswith(".exe") else name
        if name in ("ffmpeg", "ffprobe"):
            return name
        self._problem(f"a process the harness does not fake would start: {exe}")
        return ""

    def _launch(self, args, via: str, cwd=None, pipes: tuple = ()) -> tuple[_Job, list[str]]:
        argv = self._argv(args)
        tool = self._tool(argv[0])
        record: dict = {}
        if self._step:
            record["step"] = self._step
        record["argv"] = [tool, *(self.norm(a) for a in argv[1:])]
        record["via"] = via
        if pipes:
            record["pipes"] = list(pipes)
        if cwd is not None:
            record["cwd"] = self.norm(os.fspath(cwd))
        self._attach(record, argv, cwd)
        self._leftovers(record, "a command")
        self.commands.append(record)
        job = self._ffprobe(record, argv) if tool == "ffprobe" else self._ffmpeg(record, argv, cwd)
        return job, argv

    def _attach(self, record: dict, argv: list[str], cwd) -> None:
        """The .ass files and concat listings the command reads, as text."""
        ass: dict = {}
        for arg in argv[1:]:
            refs = [m.group(1) for m in _SUBTITLES.finditer(arg)]
            if arg.lower().endswith(".ass") and not refs:
                refs = [arg]
            for ref in refs:
                path = Path(ref)
                if not path.is_absolute():
                    path = Path(cwd if cwd is not None else os.getcwd()) / path
                ass[self.norm(ref)] = (path.read_text(encoding="utf-8").splitlines()
                                       if path.is_file() else "<not there when the command ran>")
        if ass:
            record["ass"] = ass
        listings: dict = {}
        for listing in self._concat_inputs(argv):
            lines = (listing.read_text(encoding="utf-8").splitlines()
                     if listing.is_file() else "<not there when the command ran>")
            listings[self.norm(str(listing))] = (
                [self.norm(line) for line in lines] if isinstance(lines, list) else lines)
        if listings:
            record["listing"] = listings

    def _leftovers(self, value, where: str) -> None:
        """Fail on a path the normalising missed: a snapshot holding one
        would only ever match on the machine that recorded it."""
        for text in _strings(value):
            if _DRIVE_PATH.search(text) or _POSIX_PATH.search(text):
                self._problem(f"{where} holds a machine-specific path the harness doesn't know: {text}")
            for marker in _OUTSIDE:
                if marker in text:
                    self._problem(f"{where} holds a path outside the test's folder: {text}")

    # ---- ffprobe ------------------------------------------------------------------------

    def _ffprobe(self, record: dict, argv: list[str]) -> _Job:
        path = Path(argv[-1])
        info = self.info(path)
        if info is None:
            if path.exists():
                self._problem(f"ffprobe of a file the harness didn't make: {self.norm(str(path))}")
            return _Job(record, 1, "", f"{argv[-1]}: No such file or directory")
        entries = _after(argv, "-show_entries")
        fps = _fps(info["rate"])
        if "-show_streams" in argv and _after(argv, "-of") == "json":
            return _Job(record, 0, _streams_json(info))
        if entries == "format=duration":
            return _Job(record, 0, f"{float(info['duration']):.6f}\n")
        if entries == "stream=codec_name":
            return _Job(record, 0, f"{info['codec']}\n")
        if entries == "stream=width,height:stream_side_data=rotation":
            return _Job(record, 0, f"width={info['w']}\nheight={info['h']}\n")
        if entries == "stream=codec_name,pix_fmt,width,height":
            return _Job(record, 0, f"codec_name={info['codec']}\npix_fmt={info['pix_fmt']}\n"
                                   f"width={info['w']}\nheight={info['h']}\n")
        if entries == "frame=pts_time":
            count = 5 if _after(argv, "-read_intervals") == "%+#5" else max(1, round(info["duration"] * fps))
            first = float(info.get("first_frame") or 0.0)
            return _Job(record, 0, "".join(f"{first + i / fps:.6f}\n" for i in range(count)))
        self._problem("an ffprobe query the harness can't answer: " + " ".join(record["argv"]))
        return _Job(record, 1)

    # ---- ffmpeg -------------------------------------------------------------------------

    def _ffmpeg(self, record: dict, argv: list[str], cwd) -> _Job:
        if self._failing.get("ffmpeg"):
            self._failing["ffmpeg"] -= 1
            return _Job(record, 1, "", "Error while encoding (failed on purpose by the test harness)")
        if "lavfi" in argv and argv[-3:] == ["-f", "null", "-"]:
            # video/encoding.py's start-up probe of a hardware encoder.
            from video import encoding

            wanted = encoding._CANDIDATES.get(self.encoder, [])
            works = self.encoder != "cpu" and _after(argv, "-c:v") == wanted[1]
            return _Job(record, 0 if works else 1, "", "" if works else "Unknown encoder (harness)")
        target = argv[-1]
        if target == "-" or target.startswith("pipe:"):
            stream = 0
            if _after(argv, "-pix_fmt") == "bgr24" and argv[-3:-1] == ["-f", "rawvideo"]:
                info = self._input_info(argv) or DEFAULT_MEDIA
                select = re.search(r"mod\(n\\?,(\d+)\)", " ".join(argv))
                every = int(select.group(1)) if select else 1
                stream = -(-int(info["frames"]) // every) * int(info["w"]) * int(info["h"]) * 3
            return _Job(record, 0, stream=stream)
        output = Path(target)
        if not output.is_absolute():
            output = Path(cwd if cwd is not None else os.getcwd()) / output
        if not output.parent.is_dir():
            return _Job(record, 1, "", f"{target}: No such file or directory")
        return _Job(record, 0, output=output, info=self._output_info(argv))

    def _output_info(self, argv: list[str]) -> dict:
        from video.encoding import FINAL_MOVFLAGS

        info = copy.deepcopy(self._input_info(argv) or DEFAULT_MEDIA)
        if FINAL_MOVFLAGS in argv:
            info["w"], info["h"] = self.canvas
        if "-t" in argv[:-1]:
            info["duration"] = round(float(argv[argv.index("-t") + 1]), 6)
        return info

    def _input_info(self, argv: list[str]) -> dict | None:
        """The descriptor of the command's first input that is a file."""
        fmt = None
        for i, arg in enumerate(argv[:-1]):
            if arg == "-f":
                fmt = argv[i + 1]
            elif arg == "-i":
                value, this, fmt = argv[i + 1], fmt, None
                if this in ("lavfi", "rawvideo") or value == "-" or value.startswith("pipe:"):
                    continue
                if this == "concat":
                    parts = _listed(Path(value))
                    found = self.info(parts[0]) if parts else None
                else:
                    found = self.info(Path(value))
                if found:
                    return found
        return None

    def _concat_inputs(self, argv: list[str]) -> list[Path]:
        fmt, out = None, []
        for i, arg in enumerate(argv[:-1]):
            if arg == "-f":
                fmt = argv[i + 1]
            elif arg == "-i":
                if fmt == "concat":
                    out.append(Path(argv[i + 1]))
                fmt = None
        return out

    def _finish(self, job: _Job) -> None:
        if job.output is not None and job.returncode == 0:
            job.output.write_text(MEDIA_MARK + json.dumps(job.info, sort_keys=True), encoding="utf-8")

    def _run(self, args, *_pos, **kwargs):
        job, argv = self._launch(args, "run", kwargs.get("cwd"))
        self._finish(job)
        text = bool(kwargs.get("text") or kwargs.get("universal_newlines")
                    or kwargs.get("encoding") or kwargs.get("errors"))
        stdout = job.stdout if text else job.stdout.encode()
        stderr = job.stderr if text else job.stderr.encode()
        if kwargs.get("check") and job.returncode:
            raise subprocess.CalledProcessError(job.returncode, argv, stdout, stderr)
        return subprocess.CompletedProcess(argv, job.returncode, stdout, stderr)

    def _call(self, args, *pos, **kwargs) -> int:
        return self._run(args, *pos, **kwargs).returncode

    def _check_call(self, args, *pos, **kwargs) -> int:
        return self._run(args, *pos, **{**kwargs, "check": True}).returncode

    def _check_output(self, args, *pos, **kwargs):
        return self._run(args, *pos, **{**kwargs, "check": True}).stdout


class _FakePopen:
    def __init__(self, harness: RenderHarness, args, bufsize=-1, executable=None, stdin=None, stdout=None,
                 stderr=None, *_pos, cwd=None, text=None, universal_newlines=None, encoding=None,
                 errors=None, **_kwargs):
        pipes = tuple(name for name, value in (("stdin", stdin), ("stdout", stdout))
                      if value == subprocess.PIPE)
        self._harness = harness
        self._job, self.args = harness._launch(args, "Popen", cwd, pipes)
        self.pid = 4242
        self.returncode = None
        as_text = bool(text or universal_newlines or encoding or errors)
        self.stdin = _Sink() if stdin == subprocess.PIPE else None
        self.stdout = (_Source(self._job.stream, self._ended, as_text)
                       if stdout == subprocess.PIPE else None)
        self.stderr = _Source(0, None, as_text) if stderr == subprocess.PIPE else None

    def _ended(self) -> None:
        if self.returncode is None:
            self._done()

    def _done(self, killed: bool = False) -> None:
        if self.returncode is not None:
            return
        if killed:
            self.returncode = -9
            return
        if self.stdin is not None:
            self._job.record["stdin_bytes"] = self.stdin.count
        self._harness._finish(self._job)
        self.returncode = self._job.returncode

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self._done()
        return self.returncode

    def communicate(self, input=None, timeout=None):
        if input is not None and self.stdin is not None:
            self.stdin.write(input)
        out = self.stdout.read() if self.stdout is not None else None
        err = self.stderr.read() if self.stderr is not None else None
        self._done()
        return out, err

    def kill(self) -> None:
        self._done(killed=True)

    terminate = kill

    def send_signal(self, _sig) -> None:
        self.kill()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        for stream in (self.stdin, self.stdout, self.stderr):
            if stream is not None:
                stream.close()
        self.wait()
        return False


def _streams_json(info: dict) -> str:
    streams = [{
        "index": 0,
        "codec_name": info["codec"],
        "codec_type": "video",
        "width": info["w"],
        "height": info["h"],
        "pix_fmt": info["pix_fmt"],
        "r_frame_rate": info["rate"],
        "avg_frame_rate": info["rate"],
        "time_base": info["time_base"],
        "duration": f"{float(info['duration']):.6f}",
    }]
    audio = info.get("audio")
    if audio:
        streams.append({
            "index": 1,
            "codec_name": "aac",
            "codec_type": "audio",
            "sample_rate": str(audio["sample_rate"]),
            "channels": audio["channels"],
            "time_base": f"1/{audio['sample_rate']}",
            "duration": f"{float(info['duration']):.6f}",
        })
    return json.dumps({"streams": streams}, indent=4)


def _listed(listing: Path) -> list[Path]:
    """The files a concat listing names, in order."""
    try:
        lines = listing.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        match = _LISTED.match(line.strip())
        if match:
            out.append(Path(match.group(1).replace("'\\''", "'")))
    return out


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            if k != "ass":          # caption text: words, not paths
                yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


_REPO_PREFIX = os.path.normcase(str(ROOT)) + os.sep


def _in_repo(module) -> bool:
    """Whether a loaded module is one of this repo's files. By string, not
    resolve(): it is asked of every loaded module, thousands with torch."""
    file = getattr(module, "__file__", None)
    if not isinstance(file, str):
        return False
    return os.path.normcase(os.path.abspath(file)).startswith(_REPO_PREFIX)


# ---- snapshots ------------------------------------------------------------------------------


def dump(data) -> str:
    """A snapshot as it is written: two-space JSON, LF line ends."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def check_snapshot(path: Path, actual: dict, update: bool) -> None:
    """Compare `actual` with the snapshot at `path`. With `update`, write it
    instead. A missing snapshot fails: CI never creates one by itself."""
    text = dump(actual)
    if update:
        path.parent.mkdir(parents=True, exist_ok=True)
        old = path.read_text(encoding="utf-8") if path.exists() else None
        if old is None or dump(json.loads(old)) != text:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        return
    rel = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)
    if not path.exists():
        _fail(f"No snapshot at {rel}. Record it from the code as it is now with\n"
              f"  python -m pytest {Path(__file__).parent.name}/test_render_argv_snapshots.py --update-snapshots\n"
              "and review it before committing.")
    expected = dump(json.loads(path.read_text(encoding="utf-8")))
    if expected == text:
        return
    diff = list(difflib.unified_diff(expected.splitlines(), text.splitlines(),
                                     f"{rel} (snapshot)", "this run", n=2, lineterm=""))
    shown = diff[:60]
    if len(diff) > len(shown):
        shown.append(f"... and {len(diff) - len(shown)} more diff lines")
    _fail(f"The FFmpeg work for {path.stem} changed:\n" + "\n".join(shown)
          + "\nIf the change is meant, rerun with --update-snapshots and review the diff.")
