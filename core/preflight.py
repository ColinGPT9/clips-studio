"""Is this install actually able to make a clip?

An installed copy has more ways to be half-ready than a dev checkout: FFmpeg
might not have shipped, the AI runtime might not have started, the model might
not be pulled, the transcription weights might be absent, the disk might be
full. Each of those used to surface as a stack trace somewhere deep in a
pipeline stage, twenty minutes into a video.

This checks them up front and says which one is wrong in words a creator can
act on. The setup wizard runs it on first launch; the app can run it any time
something looks broken.

Nothing here raises — a check that itself fails is reported as a failed check.
"""

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from core.binaries import bundled_whisper_sizes, ffmpeg, ffprobe, has_bundled_ollama

# Below this a single long video can fill the disk part-way through rendering.
MIN_FREE_GB = 20


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    fix: str = ""          # what the user should do; empty when ok
    blocking: bool = True  # False = degraded but still usable


@dataclass
class Preflight:
    checks: list[Check] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        """True when nothing BLOCKING is wrong. Degraded checks (no GPU, low
        disk) don't stop a creator from making a clip."""
        return all(c.ok or not c.blocking for c in self.checks)

    def as_dict(self) -> dict:
        return {
            "ready": self.ready,
            "checks": [
                {"name": c.name, "ok": c.ok, "detail": c.detail,
                 "fix": c.fix, "blocking": c.blocking}
                for c in self.checks
            ],
        }


def _binary_version(path: str, label: str) -> str:
    """First line of `<binary> -version`, or "" if it won't run."""
    try:
        out = subprocess.run([path, "-version"], capture_output=True, text=True,
                             timeout=30, encoding="utf-8", errors="replace")
        first = (out.stdout or "").splitlines()
        return first[0].replace(f"{label} version ", "") if first else ""
    except Exception:
        return ""


def check_ffmpeg() -> list[Check]:
    checks = []
    for label, path in (("ffmpeg", ffmpeg()), ("ffprobe", ffprobe())):
        # A bare name means nothing resolved it — not bundled, not on PATH.
        resolved = path != label or shutil.which(label) is not None
        version = _binary_version(path, label) if resolved else ""
        checks.append(Check(
            name=label,
            ok=bool(version),
            detail=version or "not found",
            fix="" if version else
                "This should ship with Clips Kitty. Reinstall the app, or "
                "install FFmpeg and put it on your PATH.",
        ))
    return checks


def check_ollama(host: str, model: str) -> list[Check]:
    """Ollama serves the LLM.

    Installed copies ship their own and Electron starts it, so this failing is
    a bug rather than an errand. A checkout borrows whatever Ollama the
    developer runs, where it means the familiar thing — so the advice given
    depends on which of the two this is.
    """
    import requests

    try:
        r = requests.get(f"{host.rstrip('/')}/api/tags", timeout=5)
        r.raise_for_status()
        installed = [m.get("name", "") for m in (r.json().get("models") or [])]
    except Exception as e:
        return [Check(
            name="ollama",
            ok=False,
            detail=f"not reachable at {host} ({type(e).__name__})",
            fix="The AI runtime that ships with Clips Kitty did not start. "
                "Restarting the app usually fixes it. If it keeps happening, "
                "please report it — this one is not your fault."
                if has_bundled_ollama() else
                "Install Ollama from https://ollama.com and let it run in the "
                "background. Clips Kitty uses it for the AI that picks and "
                "titles clips.",
        )]

    checks = [Check(name="ollama", ok=True,
                    detail=f"running, {len(installed)} model(s) installed")]

    # Ask whether a model can be used, NOT whether one particular model is
    # present. Demanding the configured tag failed Store certification: setup
    # downloads the model it recommends for your hardware, which on a machine
    # with no graphics card is not the one settings.yaml ships with, so a
    # reviewer watched gemma3:4b reach 100% and still be told gemma:7b was
    # missing. "The recommended model is absent" and "there is no AI at all"
    # are completely different situations and only the second is a failure.
    from llm.manager import model_is_installed, resolve_usable_model

    usable = (
        model.split("/")[-1]
        if model and model_is_installed(model, installed)
        else resolve_usable_model(host, model)
    )
    checks.append(Check(
        name="model",
        ok=bool(usable),
        detail=f"{usable} installed" if usable else "no AI model installed",
        # No `ollama pull` hint for an installed copy: the bundled runtime has
        # its own port and its own model folder, so a command typed into a
        # terminal would download into a store this app never reads.
        fix="" if usable else
            "Download a model from the Models page." if has_bundled_ollama() else
            "Download one from the Models page, or run: ollama pull gemma3:4b",
    ))
    return checks


def check_whisper(configured: str) -> Check:
    """Transcription weights.

    Not blocking, because faster-whisper can still fetch them from Hugging
    Face and produce a correct transcript. But that fetch happens with no
    progress and no explanation, several minutes into a job, and looks exactly
    like a hang — so it is worth saying up front that it is going to happen.
    """
    have = bundled_whisper_sizes()
    if not have:
        return Check(
            name="whisper",
            ok=False,
            blocking=False,
            detail="no transcription weights bundled",
            fix="The first video will pause to download them (1-2 GB). That "
                "is a one-off, and it works — it just looks like nothing is "
                "happening while it runs.",
        )

    # "auto" resolves at load time by device, so both sizes have to be there
    # for it to be satisfied whichever machine this turns out to be.
    wanted = ("large-v3-turbo", "small") if configured == "auto" else (configured,)
    absent = [size for size in wanted if size not in have]
    return Check(
        name="whisper",
        ok=not absent,
        blocking=False,
        detail=f"{', '.join(have)} bundled",
        fix="" if not absent else
            f"{' and '.join(absent)} will be downloaded on first use, which "
            f"pauses that video for a few minutes.",
    )


def check_gpu() -> Check:
    """Not blocking: the app works on CPU, just slowly. Worth saying so
    plainly rather than letting someone conclude it's broken."""
    from core.gpu import NO_GPU, cuda_usable

    usable, reason = cuda_usable()

    if usable:
        try:
            import torch

            vram = torch.cuda.get_device_properties(0).total_memory / 1e9
            return Check(name="gpu", ok=True, detail=f"{reason} ({vram:.0f} GB)",
                         blocking=False)
        except Exception:
            # The GPU works; only the size lookup failed. Not worth a warning.
            return Check(name="gpu", ok=True, detail=reason, blocking=False)

    # A GPU that is present but unusable used to be reported here as ok=True,
    # so someone with a card newer than the bundled PyTorch was told their GPU
    # was fine right up until the job crashed on it. It is still not blocking —
    # CPU genuinely works — but it is not "fine", and the two cases share no
    # remedy at all.
    if reason == NO_GPU:
        fix = ("Clips Kitty works without a GPU, but processing is much "
               "slower. An NVIDIA GPU gives the biggest speed-up.")
    else:
        fix = ("Processing will run on the CPU, which works but is slower. "
               "Nothing to do on your end: this needs a Clips Kitty build "
               "made against a newer CUDA, which a future release will carry.")

    return Check(name="gpu", ok=False, blocking=False,
                 detail=f"{reason} — running on CPU", fix=fix)


def check_disk(data_dir: Path) -> Check:
    try:
        target = data_dir if data_dir.exists() else data_dir.parent
        free_gb = shutil.disk_usage(target).free / 1e9
        ok = free_gb >= MIN_FREE_GB
        return Check(
            name="disk",
            ok=ok,
            blocking=False,
            detail=f"{free_gb:.0f} GB free",
            fix="" if ok else
                f"Under {MIN_FREE_GB} GB free. Source videos and clips are "
                f"large — clear space, or use Storage cleanup in Settings.",
        )
    except Exception as e:
        return Check(name="disk", ok=False, blocking=False,
                     detail=f"could not check ({type(e).__name__})")


def run(config: dict) -> Preflight:
    """Every check, in the order a creator would care about them."""
    pf = Preflight()
    pf.checks += check_ffmpeg()

    llm = config.get("llm") or {}
    host = llm.get("ollama_host") or "http://localhost:11434"
    # `llm.backend` is what the pipeline actually loads (llm/registry.py), and
    # load_config derives it from the flat `model:` key. Reading that key here
    # instead was a second source of truth, and defaulting it to a hard-coded
    # gemma:7b made the check demand a model nobody had chosen.
    model = (llm.get("backend") or config.get("model") or "").split("/")[-1]
    pf.checks += check_ollama(host, model)

    whisper = (config.get("whisper") or {}).get("model") or "auto"
    pf.checks.append(check_whisper(str(whisper)))

    pf.checks.append(check_gpu())
    pf.checks.append(check_disk(Path(config.get("paths", {}).get("data_dir", "data"))))
    return pf
