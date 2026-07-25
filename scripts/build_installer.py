"""Build the Windows installer, end to end.

    python scripts/build_installer.py

Runs the whole chain in order and stops at the first failure with an
explanation rather than a stack trace:

    1. check the build tools are present
    2. fetch FFmpeg if it isn't vendored yet
    3. freeze the Python engine to build/dist/backend/api.exe
    4. build the Electron front end
    5. wrap both in an NSIS installer -> release/

The result is release/ClipsStudio-Setup-<version>.exe, which installs the
app, the Python engine, FFmpeg and the YOLO weights together. The only
thing a creator still needs is Ollama and a model, which the app's setup
wizard handles.

Flags:
    --skip-backend    reuse the frozen backend from a previous run
    --skip-ui         reuse the previously built renderer
    --backend-only    stop after freezing the engine (fast iteration)
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
BACKEND_OUT = ROOT / "build" / "dist" / "backend"


def say(step: str, message: str) -> None:
    print(f"\n=== {step}: {message}", flush=True)


def run(cmd: list[str], cwd: Path, what: str) -> None:
    # npm and npx are .CMD batch files on Windows, and CreateProcess cannot
    # run those by bare name — it needs the resolved path. Resolving here
    # (rather than using shell=True) keeps arguments from being re-parsed by
    # cmd.exe, which matters for paths with spaces.
    exe = shutil.which(str(cmd[0]))
    if exe is None:
        sys.exit(f"\n{what} failed: '{cmd[0]}' is not on PATH.")
    resolved = [exe, *[str(c) for c in cmd[1:]]]

    print(f"    $ {' '.join(resolved)}", flush=True)
    started = time.time()
    try:
        result = subprocess.run(resolved, cwd=cwd, shell=False)
    except OSError as e:
        sys.exit(f"\n{what} failed to start: {e}")
    if result.returncode != 0:
        sys.exit(f"\n{what} failed (exit {result.returncode}). Nothing was packaged.")
    print(f"    done in {time.time() - started:.0f}s", flush=True)


def check_tools(skip_ui: bool) -> None:
    say("1/5", "checking build tools")
    missing = []

    try:
        import PyInstaller  # noqa: F401

        print("    PyInstaller: ok")
    except ImportError:
        missing.append("PyInstaller — run: pip install -r requirements-build.txt")

    if not skip_ui:
        npm = shutil.which("npm")
        print(f"    npm: {npm or 'NOT FOUND'}")
        if not npm:
            missing.append("npm — install Node.js 18+ from https://nodejs.org")

    if missing:
        sys.exit("\nMissing build tools:\n  - " + "\n  - ".join(missing))


def ensure_ffmpeg() -> None:
    say("2/5", "checking vendored FFmpeg")
    vendor = ROOT / "vendor" / "ffmpeg"
    have = all((vendor / n).exists() for n in ("ffmpeg.exe", "ffprobe.exe"))
    if have:
        size = sum(f.stat().st_size for f in vendor.iterdir() if f.is_file())
        print(f"    present ({size / 1e6:.0f} MB)")
        return
    print("    not vendored yet — fetching")
    run([sys.executable, str(ROOT / "scripts" / "fetch_ffmpeg.py")], ROOT, "FFmpeg download")


def freeze_backend() -> None:
    say("3/5", "freezing the Python engine (several minutes, PyTorch is large)")
    run(
        [sys.executable, "-m", "PyInstaller", str(ROOT / "clips-studio.spec"),
         "--noconfirm", "--distpath", str(ROOT / "build" / "dist"),
         "--workpath", str(ROOT / "build" / "work"), "--log-level", "WARN"],
        ROOT,
        "Freezing the backend",
    )
    exe = BACKEND_OUT / "api.exe"
    if not exe.exists():
        sys.exit(f"\nExpected {exe} but it wasn't produced.")
    total = sum(f.stat().st_size for f in BACKEND_OUT.rglob("*") if f.is_file())
    print(f"    backend: {exe} ({total / 1e9:.2f} GB unpacked)")


def smoke_test_backend() -> None:
    """A frozen build that can't import its own dependencies is the classic
    PyInstaller failure, and it only shows up at runtime. Catch it here
    rather than in an installer someone already downloaded."""
    say("3b/5", "smoke-testing the frozen engine")
    exe = BACKEND_OUT / "api.exe"
    result = subprocess.run([str(exe), "status"], capture_output=True, text=True,
                            timeout=300, cwd=ROOT)
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        print(output[-2000:])
        sys.exit("\nThe frozen engine failed to run. Fix the spec before packaging.")
    print(f"    engine runs (exit 0, {len(output)} bytes of output)")


def build_ui() -> None:
    say("4/5", "building the desktop front end")
    if not (UI / "node_modules").exists():
        run(["npm", "install"], UI, "npm install")
    run(["npm", "run", "build"], UI, "Renderer build")


def package_installer() -> None:
    say("5/5", "packaging the installer")
    run(["npx", "electron-builder", "--win", "--config", "electron-builder.yml"],
        UI, "electron-builder")
    release = ROOT / "release"
    installers = sorted(release.glob("*.exe")) if release.exists() else []
    if not installers:
        sys.exit("\nelectron-builder reported success but produced no .exe")
    for path in installers:
        print(f"\n    INSTALLER: {path}  ({path.stat().st_size / 1e9:.2f} GB)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-backend", action="store_true",
                    help="reuse the frozen backend from a previous run")
    ap.add_argument("--skip-ui", action="store_true",
                    help="reuse the previously built renderer")
    ap.add_argument("--backend-only", action="store_true",
                    help="stop after freezing the engine")
    args = ap.parse_args()

    started = time.time()
    check_tools(args.skip_ui)
    ensure_ffmpeg()

    if args.skip_backend:
        print("\n=== 3/5: skipped (reusing existing backend)")
        if not (BACKEND_OUT / "api.exe").exists():
            sys.exit("--skip-backend given but no frozen backend exists yet.")
    else:
        freeze_backend()
        smoke_test_backend()

    if args.backend_only:
        print(f"\nBackend only — stopped after freezing. {time.time() - started:.0f}s")
        return

    if args.skip_ui:
        print("\n=== 4/5: skipped (reusing existing renderer build)")
    else:
        build_ui()

    package_installer()
    print(f"\nBuild finished in {(time.time() - started) / 60:.1f} minutes.")


if __name__ == "__main__":
    main()
