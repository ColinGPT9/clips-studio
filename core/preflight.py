"""Is this install actually able to make a clip?

An installed copy has more ways to be half-ready than a dev checkout: FFmpeg
might not have shipped, Ollama might not be installed, the model might not be
pulled, the disk might be full. Each of those used to surface as a stack
trace somewhere deep in a pipeline stage, twenty minutes into a video.

This checks them up front and says which one is wrong in words a creator can
act on. The setup wizard runs it on first launch; the app can run it any time
something looks broken.

Nothing here raises — a check that itself fails is reported as a failed check.
"""

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from core.binaries import ffmpeg, ffprobe

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
                "This should ship with Clips Studio. Reinstall the app, or "
                "install FFmpeg and put it on your PATH.",
        ))
    return checks


def check_ollama(host: str, model: str) -> list[Check]:
    """Ollama serves the LLM. It is deliberately NOT bundled — it is a
    separate product with its own installer and its own GPU handling."""
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
            fix="Install Ollama from https://ollama.com and let it run in the "
                "background. Clips Studio uses it for the AI that picks and "
                "titles clips.",
        )]

    checks = [Check(name="ollama", ok=True,
                    detail=f"running, {len(installed)} model(s) installed")]

    # Ollama reports "gemma:7b"; a config may say "gemma:7b" or "ollama/gemma:7b".
    wanted = model.split("/")[-1]
    have = any(m == wanted or m.startswith(f"{wanted}:") or m.split(":")[0] == wanted
               for m in installed)
    checks.append(Check(
        name="model",
        ok=have,
        detail=f"{wanted} installed" if have else f"{wanted} not installed",
        fix="" if have else
            f"Download it from the Models page, or run: ollama pull {wanted}",
    ))
    return checks


def check_gpu() -> Check:
    """Not blocking: the app works on CPU, just slowly. Worth saying so
    plainly rather than letting someone conclude it's broken."""
    try:
        import torch

        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            vram = torch.cuda.get_device_properties(0).total_memory / 1e9
            return Check(name="gpu", ok=True, detail=f"{name} ({vram:.0f} GB)",
                         blocking=False)
        return Check(
            name="gpu", ok=False, blocking=False,
            detail="no CUDA GPU detected — running on CPU",
            fix="Clips Studio works without a GPU, but processing is much "
                "slower. An NVIDIA GPU gives the biggest speed-up.",
        )
    except Exception as e:
        return Check(name="gpu", ok=False, blocking=False,
                     detail=f"could not check ({type(e).__name__})")


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
    model = config.get("model") or "gemma:7b"
    pf.checks += check_ollama(host, model)

    pf.checks.append(check_gpu())
    pf.checks.append(check_disk(Path(config.get("paths", {}).get("data_dir", "data"))))
    return pf
