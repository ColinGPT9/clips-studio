"""Run the automated compatibility checks for one listed version: what earns ✓ Compatible.

    python scripts/check_compatibility.py clips-kitty-examples/scene-cut-highlights
    python scripts/check_compatibility.py example-dev/example-plugin --version 1.2.0 --write

The checks, in order; when one fails, the ones after it are not run:

    manifest_valid     the manifest at the listed commit passes the validator the app uses
    installs           the plugin manager fetches and installs it, as the app does
    requirements_met   this Clips Kitty is a version it accepts; it doesn't need a graphics card
                       (the check machine has none); a Python is found if it needs one; and the
                       models it lists download, up to --max-model-gb in all
    starts             it runs on a generated 40-second sample video and finishes without an error
    valid_result       its answer passes the result contract (moments inside the video), even when
                       it finds none in the sample

✓ Compatible means exactly these passed, for one version at one commit, on one
Clips Kitty version. It is a technical label: nobody reads the code, and a
plugin can pass and still do something its listing doesn't say.

This installs and RUNS the plugin's code. Run it only on a throwaway machine
holding no secrets and nothing else of value (a CI job, a container). With
--write, the record is added to the catalog's stats/compatibility.json; the
index build turns a passed record for a listing's exact version and commit
into the ✓ Compatible badge.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CHECKS = ("manifest_valid", "installs", "requirements_met", "starts", "valid_result")
SAMPLE_SECONDS = 40


def make_sample_video(path: Path, ffmpeg: str) -> Path:
    """40 seconds: four 10-second colour scenes, a quiet tone, and one loud
    stretch (22-27 s), so a pipeline has cuts and sound to look at."""
    volume = "if(between(t,22,27),0.9,0.02)"
    cmd = [ffmpeg, "-loglevel", "error", "-y"]
    for colour in ("red", "blue", "green", "white"):
        cmd += ["-f", "lavfi", "-i", f"color=c={colour}:s=320x180:d=10:r=15"]
    cmd += ["-f", "lavfi", "-i", f"sine=f=440:d={SAMPLE_SECONDS},volume='{volume}':eval=frame",
            "-filter_complex", "[0][1][2][3]concat=n=4:v=1:a=0[v]", "-map", "[v]", "-map", "4:a",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)]
    subprocess.run(cmd, check=True, timeout=300)
    return path


def _find(index: dict, plugin_id: str, version: str | None) -> tuple[dict, dict]:
    for listing in index.get("plugins") or []:
        if listing.get("id") == plugin_id:
            for v in listing.get("versions") or []:
                if version in (None, v.get("version")):
                    return listing, v
            raise SystemExit(f"{plugin_id} has no listed version {version}")
    raise SystemExit(f"{plugin_id} is not in the index; build it first (scripts/build_registry_index.py)")


def check(listing: dict, version: dict, *, app_version: str, work: Path, git: str | None = None, fetcher=None,
          model_fetcher=None, model_json=None, max_model_bytes: int = 2 * 1024**3, timeout: float = 600.0,
          now: str | None = None, log=print) -> dict:
    """One compatibility record. Never raises for the plugin's sake: a
    failure is a check marked false, with the reason in `note`."""
    from core.models import DownloadedVideo
    from plugins import manager, registry, runner, sources, store
    from plugins import models as plugin_models
    from plugins._sdk import host, manifest

    record = {"version": version["version"], "commit": version["commit"], "app_version": app_version,
              "plugin_api": None, "checked_at": now or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "checks": {}, "passed": False}

    def fail(name: str, why: str) -> dict:
        record["checks"][name] = False
        record["note"] = f"{name}: {why}"[:500]
        log(f"  ✗ {name}: {why}")
        return record

    def ok(name: str) -> None:
        record["checks"][name] = True
        log(f"  ✓ {name}")

    data_dir = work / "data"
    files = work / "files"
    try:
        sources.fetch(registry.source_for(listing, version), files, git=git, fetcher=fetcher)
    except sources.SourceError as e:
        return fail("manifest_valid", f"couldn't get the files at commit {version['commit'][:7]}: {e}")
    data, report = manifest.validate_folder(files)
    record["plugin_api"] = ((data or {}).get("requires") or {}).get("plugin_api")
    if not report.ok:
        return fail("manifest_valid", "; ".join(report.errors))
    if data.get("id") != listing["id"] or data.get("version") != version["version"]:
        return fail("manifest_valid", f"the files are {data.get('id')} {data.get('version')}, not what is listed")
    ok("manifest_valid")

    # The plugin manager installs the files just fetched, exactly as it would after its own fetch.
    try:
        plan = manager.plan(data_dir, {"kind": "folder", "path": str(files)}, app_version=app_version,
                            tier=registry.listing_tier(listing),
                            expect={"id": listing["id"], "version": version["version"]})
    except manager.ManagerError as e:
        return fail("installs", str(e))
    unmet = [e for e in plan["errors"] if e.startswith("Can't install:")]
    if unmet:  # the app version: a requirement, which the app checks before installing
        return fail("installs", "; ".join(unmet))
    if not plan["ok"]:
        return fail("installs", "; ".join(plan["errors"]))
    try:
        manager.install(data_dir, plan["plan_id"], app_version=app_version)
    except manager.ManagerError as e:
        return fail("installs", str(e))
    plugin = store.get(data_dir, listing["id"])
    if plugin is None:
        return fail("installs", "installed, but its files can't be read back")
    ok("installs")

    data = plugin.manifest
    problem = store.compatibility_problem(data, app_version)
    if problem:
        return fail("requirements_met", problem)
    if (data.get("requirements") or {}).get("gpu") == "required":
        return fail("requirements_met", "it needs a graphics card, and the check machine has none")
    python = host.find_python()
    command = (data.get("run") or {}).get("command") or []
    if "{python}" in command and not python:
        return fail("requirements_met", "it needs Python, and none was found")
    refs = plugin_models.refs_of(data)
    total = 0
    for ref in refs:
        if ref["source"] in ("ollama", "bundled"):
            return fail("requirements_met", f"its model {ref['id']} comes from {ref['source']}, which the check "
                                            "machine doesn't have")
        try:
            info = plugin_models.plan(data_dir, ref, **({"fetch_json": model_json} if model_json else {}))
        except plugin_models.ModelError as e:
            return fail("requirements_met", f"its model {ref['id']}: {e}")
        if info.get("problem"):
            return fail("requirements_met", f"its model {ref['id']}: {info['problem']}")
        if not info.get("size_known"):
            return fail("requirements_met", f"its model {ref['id']} doesn't say how big it is")
        total += int(info.get("download_bytes") or 0)
        if total > max_model_bytes:
            return fail("requirements_met", f"its models are over {max_model_bytes / 1024**3:g} GB, more than "
                                            "the check downloads")
        try:
            plugin_models.download(data_dir, ref, allow_pickle=False,
                                   **({"fetcher": model_fetcher} if model_fetcher else {}),
                                   **({"fetch_json": model_json} if model_json else {}))
        except plugin_models.ModelError as e:
            return fail("requirements_met", f"its model {ref['id']}: {e}")
    ok("requirements_met")

    from core.binaries import ffmpeg

    video_path = make_sample_video(work / "sample.mp4", ffmpeg())
    video = DownloadedVideo(video_id="compatibility-sample", title="Clips Kitty compatibility sample",
                            path=video_path, duration=float(SAMPLE_SECONDS))
    config = {"clips": {"min_duration": 5, "max_duration": 60, "max_clips_per_video": 0}, "llm": {}}
    old_timeout = runner.timeout_seconds
    runner.timeout_seconds = lambda m: min(old_timeout(m), timeout)
    try:
        clips = runner.find_clips({"id": listing["id"]}, video=video, segments=[], language="en", config=config,
                                  data_dir=data_dir)
    except runner.PluginError as e:
        text = str(e)
        if "gave an answer Clips Kitty can't use" in text:
            ok("starts")
            return fail("valid_result", text)
        return fail("starts", text)
    finally:
        runner.timeout_seconds = old_timeout
    ok("starts")
    ok("valid_result")
    record["moments"] = len(clips)
    record["passed"] = all(record["checks"].get(c) is True for c in CHECKS)
    return record


def main(argv=None) -> int:
    from plugins import registry, store

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("id", help="the listing's id, publisher/name")
    ap.add_argument("--version", help="the listed version (default: the latest)")
    ap.add_argument("--catalog", type=Path, default=registry.catalog_path(), help="the catalog folder")
    ap.add_argument("--app-version", help="the Clips Kitty version to check against (default: this checkout's)")
    ap.add_argument("--max-model-gb", type=float, default=2.0, help="the most model data it downloads (default 2)")
    ap.add_argument("--timeout", type=float, default=600.0, help="seconds the plugin may run (default 600)")
    ap.add_argument("--write", action="store_true", help="add the record to stats/compatibility.json")
    args = ap.parse_args(argv)

    if not shutil.which("ffmpeg"):
        print("the check needs FFmpeg on PATH to make its sample video", file=sys.stderr)
        return 2
    index = json.loads((args.catalog / "index.json").read_text(encoding="utf-8"))
    listing, version = _find(index, args.id, args.version)
    app_version = args.app_version or store.app_version()
    print(f"{listing['id']} {version['version']} at {version['commit'][:7]}, on Clips Kitty {app_version}")
    with tempfile.TemporaryDirectory(prefix="clipskitty-compat-") as tmp:
        record = check(listing, version, app_version=app_version, work=Path(tmp),
                       max_model_bytes=int(args.max_model_gb * 1024**3), timeout=args.timeout)
    print(("passed" if record["passed"] else "did not pass") + (f": {record['moments']} moment(s) in the sample"
                                                                if "moments" in record else ""))
    if args.write:
        path = args.catalog / "stats" / "compatibility.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        records = [r for r in data.get(listing["id"]) or [] if not (
            r.get("version") == record["version"] and r.get("commit") == record["commit"]
            and r.get("app_version") == record["app_version"])]
        data[listing["id"]] = sorted([*records, record], key=lambda r: (r["version"], r["checked_at"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(sorted(data.items())), indent=1, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        print(f"wrote {path}; rebuild the index to show it (scripts/build_registry_index.py)")
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
