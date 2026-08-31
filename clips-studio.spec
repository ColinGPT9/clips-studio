# PyInstaller spec — freezes the Python engine into resources/backend/api.exe
#
# Build with:  python scripts/build_installer.py     (don't call this directly)
#
# One-DIR, not one-file. One-file unpacks the whole payload to a temp folder
# on every launch, and with PyTorch in the bundle that is gigabytes of copying
# before the app answers its first request — slow and fragile. One-dir starts
# instantly and lets the installer share files properly.
#
# Console app on purpose: the backend prints its progress, and Electron reads
# or discards that. A windowed build sets sys.stdout to None, which turns
# every print() in the pipeline into a crash. Electron passes windowsHide so
# no console window is ever shown to the user.

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH)

datas = []
binaries = []
hiddenimports = []

# Packages that resolve things at runtime rather than by import statement, so
# PyInstaller's static analysis cannot see them:
#   yt_dlp       — one extractor module per site, imported by name
#   ultralytics  — model/task registry, plus its config yaml
#   faster_whisper / ctranslate2 — native libs + tokenizer assets
#   curl_cffi    — bundled libcurl binaries
#   piper        — espeak-ng phoneme data (19 MB) resolved from its own
#                  package directory at runtime, plus the espeak bridge
#   onnxruntime  — native inference libs Piper loads by name
for package in ("yt_dlp", "ultralytics", "faster_whisper", "ctranslate2",
                "curl_cffi", "piper", "onnxruntime"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# uvicorn picks its event loop, HTTP parser and websocket implementation at
# startup by importing strings, so every backend has to be pulled in.
hiddenimports += collect_submodules("uvicorn")
hiddenimports += [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]

# Imported lazily inside functions, so they are invisible to the analyser.
hiddenimports += [
    "torch",
    "torchvision",
    "cv2",
    "psutil",
    "pynvml",
    "google.auth",
    "google_auth_oauthlib",
    "googleapiclient",
    "googleapiclient.discovery",
]

# Config the app reads from disk at runtime. Prompts especially: they are
# plain text on purpose so they can be tuned without touching code, and that
# only holds if they ship as files.
datas += [
    (str(ROOT / "config" / "settings.yaml"), "config"),
    (str(ROOT / "config" / "prompts"), "config/prompts"),
    # The app version, for bug reports. ui/package.json is the only place the
    # version is written, and in a frozen build it is not on disk beside the
    # code — so every report from an installed copy said "app": "?" and could
    # not be dated. Three reports arrived that way before anyone noticed.
    # Bundling the file keeps one source of truth rather than stamping the
    # version into a generated module that could drift from it.
    (str(ROOT / "ui" / "package.json"), "."),
]

# OpenCV Haar cascades. cv2 is a hidden import above, which ships the MODULE
# and not its data directory -- so cv2.data.haarcascades resolves to a path
# that does not exist in an installed build. CascadeClassifier does not raise
# on a missing file; it returns an empty classifier and detectMultiScale then
# fails with "(-215:Assertion failed) !empty()", which reads as a rendering
# bug. That was issue #86: a job analysed a video, chose a clip, and produced
# zero clips because the crop needed the face fallback.
#
# Only the two files video/tracker.py actually loads. collect_all("cv2") would
# bring the whole data directory -- cascades for eyes, plates, bodies and the
# rest -- none of which this app ever opens, in an installer already near 7 GB.
try:
    import cv2 as _cv2

    _cascade_dir = Path(_cv2.data.haarcascades)
    for _cascade in ("haarcascade_frontalface_default.xml",
                     "haarcascade_profileface.xml"):
        _src = _cascade_dir / _cascade
        if _src.is_file():
            datas += [(str(_src), "cascades")]
except Exception:
    # A build machine without cv2 cannot produce a working app anyway, and the
    # runtime now degrades to pose-only cropping rather than crashing.
    pass

# YOLO weights. Ultralytics would otherwise download them on first use, which
# means a creator's first video stalls on a silent network fetch.
for weights in ("yolov8n-pose.pt", "yolov8n.pt"):
    if (ROOT / weights).exists():
        datas += [(str(ROOT / weights), ".")]

# Active-speaker weights (TalkNet). Same reasoning as the YOLO ones: bundled
# so an installed copy never fetches anything at runtime. Absent from a fresh
# checkout until scripts/fetch_asd_model.py runs; the build carries on without
# it and the tracker falls back to mouth-motion.
_asd = ROOT / "models" / "pretrain_TalkSet.model"
if _asd.exists():
    datas += [(str(_asd), ".")]

# FFmpeg, fetched by scripts/fetch_ffmpeg.py. core.binaries looks for an
# ffmpeg/ folder next to the executable.
vendor_ffmpeg = ROOT / "vendor" / "ffmpeg"
if vendor_ffmpeg.exists():
    for binary in vendor_ffmpeg.iterdir():
        if binary.is_file():
            datas += [(str(binary), "ffmpeg")]

# The Ollama runtime, fetched by scripts/fetch_ollama.py. Bundled so a creator
# never has to install a second program before they can make a clip.
#
# Walked recursively, unlike FFmpeg's two flat binaries: ollama.exe is useless
# without the GPU runners and CUDA libraries under lib/, and those have to keep
# their layout relative to the executable or it falls back to CPU inference.
vendor_ollama = ROOT / "vendor" / "ollama"
if vendor_ollama.exists():
    for item in vendor_ollama.rglob("*"):
        if item.is_file():
            datas += [(str(item), str(Path("ollama") / item.relative_to(vendor_ollama).parent))]

# Whisper weights, fetched by scripts/fetch_whisper.py. The quietest of the
# bundled dependencies and the one that most needed bundling: faster-whisper
# reads a bare size name as "download it from Hugging Face", so without these
# a creator's first video stalls on a silent multi-gigabyte fetch that is
# indistinguishable from a hang. core.binaries.whisper_model() finds them.
vendor_whisper = ROOT / "vendor" / "whisper"
if vendor_whisper.exists():
    for item in vendor_whisper.rglob("*"):
        if item.is_file():
            datas += [(str(item), str(Path("whisper") / item.relative_to(vendor_whisper).parent))]

# Things that bloat the bundle without being used. Matplotlib and friends
# arrive as transitive dependencies of ultralytics but nothing here plots.
#
# polars is 175 MB of that, and ultralytics declares it as a hard dependency,
# so dropping it is only safe because it is never actually imported on the
# paths this app uses. Verified rather than assumed: with polars blocked at
# import time, both YOLO models load and predict, and polars never appears in
# sys.modules. Re-check after an ultralytics upgrade — a detect/pose run that
# starts wanting a dataframe would fail at runtime, not at build time.
#
# matplotlib is NOT here, and must not be added back. Ultralytics imports it
# on the path video/tracker.py loads the model through, so excluding it made
# every clip job die at the reactions stage with "No module named
# 'matplotlib'" — in installed copies only, because a development machine has
# it sitting in site-packages. It cost a release to find. tests/test_packaging
# guards it.
excludes = [
    "polars",
    "tkinter",
    "PyQt5",
    "PySide2",
    "IPython",
    "jupyter",
    "notebook",
    "pandas",
    "sklearn",
    "scikeras",
    "tensorflow",
    "keras",
]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="api",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX corrupts some CUDA and OpenCV DLLs and saves little on a bundle
    # this size — the risk is not worth it.
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="backend",
)
