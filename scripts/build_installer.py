"""Build the Windows installer, end to end.

    python scripts/build_installer.py

Runs the whole chain in order and stops at the first failure with an
explanation rather than a stack trace:

    1. check the build tools are present
    2. fetch FFmpeg, Ollama and the Whisper weights if they aren't vendored yet
    3. freeze the Python engine to build/dist/backend/api.exe
    4. build the Electron front end
    5. wrap both in an NSIS installer -> release/

The result is release/ClipsStudio-Setup-<version>.exe, which installs the app,
the Python engine, FFmpeg, the Ollama runtime, and the YOLO, TalkNet and
Whisper weights together. A creator installs nothing else: the one remaining
download is the language model, which the app pulls itself on first launch
behind a progress bar, because it is 5 GB and the right one depends on their
GPU.

Flags:
    --skip-backend    reuse the frozen backend from a previous run
    --skip-ui         reuse the previously built renderer
    --backend-only    stop after freezing the engine (fast iteration)
"""

import argparse
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


def _vendored_size(folder: Path) -> float:
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) / 1e6


def ensure_vendored() -> None:
    """Fetch everything the installer carries but the repo doesn't store.

    These are the difference between a one-click install and a scavenger hunt.
    Each fetch script is idempotent and prints what it already has, so a
    rebuild costs a few seconds rather than re-downloading gigabytes.
    """
    say("2/5", "checking bundled dependencies")

    # (label, marker that proves it is already there, fetch script)
    wanted = [
        ("FFmpeg", ROOT / "vendor" / "ffmpeg" / "ffprobe.exe", "fetch_ffmpeg.py"),
        ("Ollama", ROOT / "vendor" / "ollama" / "ollama.exe", "fetch_ollama.py"),
        ("Whisper weights", ROOT / "vendor" / "whisper", "fetch_whisper.py"),
    ]
    for label, marker, script in wanted:
        if marker.exists():
            print(f"    {label}: present ({_vendored_size(marker if marker.is_dir() else marker.parent):.0f} MB)")
            continue
        print(f"    {label}: not vendored yet — fetching")
        run([sys.executable, str(ROOT / "scripts" / script)], ROOT, f"{label} download")


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
    if not release.exists():
        sys.exit("\nelectron-builder reported success but produced no release/ folder")

    # The web setup is small on purpose: it fetches the .7z payload from the
    # url in electron-builder.yml's publish block at install time. Both have to
    # be published together or the installer has nothing to download.
    artifacts = [p for p in sorted(release.iterdir())
                 if p.is_file() and p.suffix.lower() in (".exe", ".zip", ".7z")
                 and not p.name.startswith("__")]
    if not artifacts:
        sys.exit("\nelectron-builder reported success but produced no installer")

    # A GitHub release asset is capped at 2 GiB, and both large artifacts are
    # well past it. Saying which file goes where — and flagging anything that
    # would simply be rejected — is more use than a list of sizes.
    github_cap = 2 * 1024**3

    print()
    for path in artifacts:
        size = path.stat().st_size
        unit = f"{size / 1e9:.2f} GB" if size >= 1e9 else f"{size / 1e6:.0f} MB"
        where = "GitHub release" if size <= github_cap else "Hugging Face (over GitHub's 2 GiB cap)"
        print(f"    ARTIFACT: {path.name}  ({unit})  ->  {where}")
    print(
        "\n    Publishing (see docs/RELEASING.md):\n"
        "      Hugging Face   the .7z payload, the .zip, the Web Setup .exe,\n"
        "                     and latest.yml LAST — it is the trigger, and an\n"
        "                     install that reads it starts downloading at once.\n"
        "      GitHub release the Web Setup .exe and the notes. Nothing else\n"
        "                     fits; the payload upload is rejected outright.\n"
        "    The .zip is the offline alternative: unzip and run Clips Studio.exe."
    )


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
    ensure_vendored()

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
