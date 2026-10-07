"""Model references: what a plugin's manifest lists under `models:`, where each
one is on this PC, and fetching the ones Clips Kitty downloads.

Four sources (docs/developers/model-references.md):

    huggingface  owner/name at a 40-character commit, the files listed
    url          one file at an https address, checked against its SHA-256
    ollama       name:tag in Ollama's own store, read through its API
    bundled      a model that ships with Clips Kitty (Whisper, YOLO pose, PANNs)

Files Clips Kitty downloads go in one folder shared by every plugin,
<data_dir>/plugin-models/:

    huggingface/models--<owner>--<name>/blobs/<sha256>             the bytes, once
    huggingface/models--<owner>--<name>/snapshots/<commit>/<file>   a link to the blob
    files/<sha256>/<file name>                                     url models
    index.json                                                     what is where

The Hugging Face part follows the Hugging Face cache's layout (snapshots
pointing at blobs), with blobs named by SHA-256. Two plugins naming the same
file at the same commit get the same path, so nothing downloads twice; a file
whose SHA-256 is already here under another name is linked, not fetched. A
link is a symbolic link where the system allows one, else a hard link, else a
copy (Windows without Developer Mode refuses symbolic links); which one was
used is recorded and shown.

Ollama's models stay in Ollama's store and the app's own models stay where the
app keeps them: this module reads those, it never copies them. It never sets
HF_HUB_CACHE or anything else for the engine.

Clips Kitty never loads a plugin's model. It hands the plugin the paths
(job.json "models", see plugins/runner.py); loading is the plugin's job, in
its own process, because loading some formats runs code.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

from core.paths import discard
from plugins._sdk import manifest

log = logging.getLogger(__name__)

ROOT = "plugin-models"
HF_BASE = "https://huggingface.co"
MAX_FILE_BYTES = 64 * 1024**3
CHUNK = 1024 * 1024
SOURCES = manifest.MODEL_SOURCES
PICKLE_SUFFIXES = manifest.PICKLE_SUFFIXES
GATED = ("Its makers share this model only with people who sign in to Hugging Face and are given access. "
         "Clips Kitty can't sign in to Hugging Face for you yet.")
OLLAMA_MODELS = "Models that run in Ollama are downloaded on the Models page"

_lock = threading.Lock()


class ModelError(ValueError):
    """A model that can't be found, fetched or used. The message is for the user."""


def root(data_dir) -> Path:
    return Path(data_dir) / ROOT


def size_text(n: int) -> str:
    """A size as the Marketplace writes it (formatBytes in ui/src/renderer/src/lib/marketplace.ts).
    int(x + 0.5) rounds a half up, as Math.round does; round() would round it to even."""
    if n >= 1e9:
        return f"{int(n / 1e9 * 10 + 0.5) / 10:g} GB"
    if n >= 1e6:
        return f"{int(n / 1e6 + 0.5)} MB"
    return f"{max(1, int(n / 1e3 + 0.5))} KB"


# ---- references --------------------------------------------------------------------------


def parse(entry: dict) -> dict:
    """One `models:` entry as a reference with every key present. The manifest
    validator has checked its shape; this only normalises it."""
    if not isinstance(entry, dict) or entry.get("source") not in SOURCES:
        raise ModelError("a model reference needs a source: " + ", ".join(SOURCES))
    files = entry.get("files")
    source = entry["source"]
    if source == "url":
        files = [_url_file_name(str(entry.get("id") or ""))]
    return {
        "name": str(entry.get("name") or ""),
        "source": source,
        "id": str(entry.get("id") or ""),
        "revision": str(entry.get("revision") or "").lower() or None,
        "files": [str(f) for f in files or []],
        "sha256": str(entry.get("sha256") or "").lower() or None,
        "format": entry.get("format"),
        "license": entry.get("license"),
        "size_bytes": entry.get("size_bytes") if isinstance(entry.get("size_bytes"), int) else None,
        "gated": bool(entry.get("gated")),
    }


def refs_of(data: dict) -> list[dict]:
    """A manifest's model references, skipping any that can't be read."""
    out = []
    for entry in data.get("models") or []:
        try:
            out.append(parse(entry))
        except ModelError:
            continue
    return out


def is_pickle(name: str) -> bool:
    return str(name).lower().split("?")[0].endswith(PICKLE_SUFFIXES)


def _url_file_name(url: str) -> str:
    name = PurePosixPath(urllib.parse.urlsplit(url).path).name
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:120] or "model"


def _key(ref: dict, file: str) -> str:
    return "|".join((ref["source"], ref["id"], ref["revision"] or ref["sha256"] or "", file))


def _safe_file(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name
            or name.startswith("/")):
        raise ModelError(f"{name!r} is not a file path inside the model's repository")
    return path


def _repo_dir(data_dir, model_id: str) -> Path:
    owner, _, name = model_id.partition("/")
    if not re.match(r"^[A-Za-z0-9_.-]+$", owner) or not re.match(r"^[A-Za-z0-9_.-]+$", name):
        raise ModelError(f"{model_id!r} is not a Hugging Face model id (owner/name)")
    return root(data_dir) / "huggingface" / f"models--{owner}--{name}"


def target_path(data_dir, ref: dict, file: str) -> Path:
    """Where a downloaded file of this reference lives."""
    if ref["source"] == "huggingface":
        if not ref["revision"] or not re.match(r"^[0-9a-f]{40}$", ref["revision"]):
            raise ModelError(f"{ref['id']}: a Hugging Face model needs its full 40-character commit")
        return _repo_dir(data_dir, ref["id"]) / "snapshots" / ref["revision"] / Path(*_safe_file(file).parts)
    if ref["source"] == "url":
        if not ref["sha256"] or not re.match(r"^[0-9a-f]{64}$", ref["sha256"]):
            raise ModelError(f"{ref['id']}: a url model needs its SHA-256")
        return root(data_dir) / "files" / ref["sha256"] / _url_file_name(ref["id"])
    raise ModelError(f"Clips Kitty doesn't download {ref['source']} models")


# ---- the shared index ----------------------------------------------------------------------


def _index_path(data_dir) -> Path:
    return root(data_dir) / "index.json"


def load_index(data_dir) -> dict:
    """{"files": {key: {sha256, size, path, link, added}}}, empty when missing or unreadable."""
    try:
        data = json.loads(_index_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"files": {}}
    files = data.get("files") if isinstance(data, dict) else None
    return {"files": files if isinstance(files, dict) else {}}


def _save_index(data_dir, index: dict) -> None:
    path = _index_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".index-{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps(index, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _by_sha(index: dict, sha: str) -> list[tuple[str, dict]]:
    return [(k, v) for k, v in index["files"].items() if v.get("sha256") == sha]


def _inside(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except (OSError, ValueError):
        return False


# ---- what is on this PC ----------------------------------------------------------------------


def _bundled_path(model_id: str) -> str | None:
    """A model the app ships, where this install has it, or None."""
    from core import binaries

    if model_id.startswith("whisper:"):
        found = binaries.whisper_model(model_id.split(":", 1)[1])
        return found if Path(found).is_dir() else None
    if model_id in ("yolov8n-pose", "yolov8n"):
        found = binaries.yolo_weights(f"{model_id}.pt")
        return found if Path(found).is_absolute() and Path(found).exists() else None
    if model_id == "panns":
        try:
            from analysis import panns

            path = panns.weights_path()
        except Exception:  # an analysis import problem means "not here"
            return None
        return str(path) if path.exists() else None
    return None


BUNDLED_IDS = ("whisper:<size>", "yolov8n-pose", "yolov8n", "panns")


def _ollama_match(model_id: str, installed: list[dict], digest: str | None) -> dict | None:
    want = model_id if ":" in model_id else f"{model_id}:latest"
    for m in installed:
        name = str(m.get("name") or m.get("model") or "")
        if (name if ":" in name else f"{name}:latest") != want:
            continue
        if digest:
            have = str(m.get("digest") or "").removeprefix("sha256:")
            if not have.startswith(digest.removeprefix("sha256:")):
                return None
        return m
    return None


def status(data_dir, ref: dict, *, ollama: list[dict] | None = None, bundled=_bundled_path) -> dict:
    """Where a reference is on this PC. `installed` is True, False, or None when
    Clips Kitty can't tell (Ollama not reachable). `ollama` is Ollama's model
    list (ollama_models()), or None when it couldn't be read."""
    out = {**ref, "installed": False, "path": None, "files_status": [], "store": "", "link": None,
           "pickle_files": [f for f in ref["files"] if is_pickle(f)], "shared_with": [], "note": ""}
    source = ref["source"]
    if source == "bundled":
        path = bundled(ref["id"])
        out.update(store="ships with Clips Kitty", installed=bool(path), path=path)
        if not path:
            out["note"] = (f"{ref['id']} is not a model this copy of Clips Kitty has"
                           if not ref["id"].startswith("whisper:") and ref["id"] not in BUNDLED_IDS
                           else f"this install doesn't carry {ref['id']}")
        return out
    if source == "ollama":
        out["store"] = "Ollama"
        if ollama is None:
            out.update(installed=None, note="Ollama isn't reachable, so Clips Kitty can't tell")
            return out
        match = _ollama_match(ref["id"], ollama, ref["revision"])
        out["installed"] = match is not None
        if match:
            out["path"] = ref["id"]
            if isinstance(match.get("size"), int):
                out["size_bytes"] = match["size"]
        else:
            out["note"] = (f"download {ref['id']} on the Models page"
                           + (" (the exact version the pipeline lists)" if ref["revision"] else ""))
        return out

    index = load_index(data_dir)
    out["store"] = "Clips Kitty's shared model folder"
    total, known = 0, True
    for file in ref["files"]:
        try:
            path = target_path(data_dir, ref, file)
        except ModelError as e:
            out["note"] = str(e)
            out["files_status"].append({"file": file, "installed": False, "path": None, "size": None})
            continue
        entry = index["files"].get(_key(ref, file)) or {}
        present = path.exists()
        size = entry.get("size") if present else None
        if size is None:
            known = False
        else:
            total += size
        out["files_status"].append({"file": file, "installed": present, "path": str(path), "size": size,
                                    "link": entry.get("link") if present else None})
        if present and entry.get("link"):
            out["link"] = entry["link"]
        if present and entry.get("sha256"):
            out["shared_with"] += [k for k, _ in _by_sha(index, entry["sha256"]) if k != _key(ref, file)]
    out["installed"] = bool(out["files_status"]) and all(f["installed"] for f in out["files_status"])
    if out["installed"]:
        out["path"] = (str(target_path(data_dir, ref, ref["files"][0])) if source == "url"
                       else str(_repo_dir(data_dir, ref["id"]) / "snapshots" / ref["revision"]))
        if known:
            out["size_bytes"] = total
    return out


# ---- talking to Hugging Face and Ollama -------------------------------------------------------


class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    """Follows redirects only to https addresses (Hugging Face redirects file
    downloads to its CDN)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise urllib.error.HTTPError(newurl, code, "refused a redirect away from https", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener(local: bool = False):
    handlers = [] if local else [_HttpsOnly()]
    return urllib.request.build_opener(*handlers)


def get_json(url: str, *, timeout: float = 20.0):
    """GET a JSON document: https, or http only to this PC (Ollama)."""
    parts = urllib.parse.urlsplit(url)
    local = parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "::1")
    if parts.scheme != "https" and not local:
        raise ModelError(f"refused to fetch {url}: https only")
    req = urllib.request.Request(url, headers={"User-Agent": "ClipsKitty", "Accept": "application/json"})
    with _opener(local).open(req, timeout=timeout) as resp:
        return json.loads(resp.read(20 * 1024 * 1024).decode("utf-8"))


def fetch_https(url: str, dest: Path) -> None:
    """Download `url` to `dest` over https (the default fetcher)."""
    if urllib.parse.urlsplit(url).scheme != "https":
        raise ModelError(f"refused to download {url}: https only")
    req = urllib.request.Request(url, headers={"User-Agent": "ClipsKitty"})
    with _opener().open(req, timeout=60) as resp, open(dest, "wb") as out:
        while True:
            chunk = resp.read(CHUNK)
            if not chunk:
                break
            out.write(chunk)


def ollama_models(host: str, *, fetch_json=get_json) -> list[dict] | None:
    """The models Ollama has (GET /api/tags), or None when it can't be reached."""
    try:
        data = fetch_json(host.rstrip("/") + "/api/tags")
    except (OSError, ValueError, ModelError):
        return None
    models = data.get("models") if isinstance(data, dict) else None
    return [m for m in models if isinstance(m, dict)] if isinstance(models, list) else None


def hf_file_url(ref: dict, file: str) -> str:
    """Hugging Face's file address at the pinned commit (the resolve endpoint)."""
    path = "/".join(urllib.parse.quote(p) for p in _safe_file(file).parts)
    return f"{HF_BASE}/{ref['id']}/resolve/{ref['revision']}/{path}"


def hub_info(ref: dict, *, fetch_json=get_json) -> dict:
    """What Hugging Face says about a model at its commit: licence, gated, and
    each listed file's size and SHA-256 when the Hub stores it in LFS. Reads
    GET /api/models/<id>/revision/<commit> and the tree at that commit;
    anything it can't read is left out. Never raises."""
    out: dict = {"files": {}}
    base = f"{HF_BASE}/api/models/{ref['id']}"
    try:
        info = fetch_json(f"{base}/revision/{ref['revision']}")
        card = info.get("cardData") or {}
        if isinstance(card.get("license"), str):
            out["license"] = card["license"]
        if "gated" in info:
            out["gated"] = bool(info["gated"])
    except (OSError, ValueError, ModelError, AttributeError):
        pass
    folders = sorted({str(PurePosixPath(f).parent) for f in ref["files"]})
    for folder in folders:
        suffix = "" if folder == "." else "/" + urllib.parse.quote(folder)
        try:
            entries = fetch_json(f"{base}/tree/{ref['revision']}{suffix}")
        except (OSError, ValueError, ModelError):
            continue
        for e in entries if isinstance(entries, list) else []:
            if not isinstance(e, dict) or e.get("path") not in ref["files"]:
                continue
            lfs = e.get("lfs") if isinstance(e.get("lfs"), dict) else {}
            item = {}
            if isinstance(e.get("size"), int):
                item["size"] = e["size"]
            if isinstance(lfs.get("oid"), str) and re.match(r"^[0-9a-f]{64}$", lfs["oid"]):
                item["sha256"] = lfs["oid"]
            out["files"][e["path"]] = item
    return out


# ---- downloading -------------------------------------------------------------------------------


def _link(blob: Path, target: Path) -> str:
    """Make `target` point at `blob`: a symbolic link, else a hard link, else a copy."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        target.unlink()
    try:
        os.symlink(os.path.relpath(blob, target.parent), target)
        return "symbolic link"
    except (OSError, NotImplementedError):
        pass
    try:
        os.link(blob, target)
        return "hard link"
    except OSError:
        shutil.copy2(blob, target)
        return "copy"


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def plan(data_dir, ref: dict, *, fetch_json=get_json, ollama: list[dict] | None = None) -> dict:
    """What downloading would do: each file, its size when known, whether it
    is already here (or here under another name), and what needs the user's
    say first (pickle files). Reads Hugging Face's metadata; downloads nothing."""
    st = status(data_dir, ref, ollama=ollama)
    out = {"name": ref["name"], "source": ref["source"], "id": ref["id"], "revision": ref["revision"],
           "installed": st["installed"], "license": ref["license"], "gated": ref["gated"], "files": [],
           "download_bytes": 0, "size_known": True, "pickle_files": st["pickle_files"], "problem": None}
    if ref["source"] in ("ollama", "bundled"):
        out["problem"] = (f"{OLLAMA_MODELS}: download {ref['id']} there." if ref["source"] == "ollama"
                          else "it ships with Clips Kitty; nothing to download")
        return out
    hub = hub_info(ref, fetch_json=fetch_json) if ref["source"] == "huggingface" else {"files": {}}
    if hub.get("license") and not out["license"]:
        out["license"] = hub["license"]
    if hub.get("gated"):
        out["gated"] = True
    index = load_index(data_dir)
    for item in st["files_status"]:
        meta = hub["files"].get(item["file"], {})
        sha = ref["sha256"] if ref["source"] == "url" else meta.get("sha256")
        size = meta.get("size") or (ref["size_bytes"] if ref["source"] == "url" else None)
        already = None
        if not item["installed"] and sha:
            already = next((k for k, _ in _by_sha(index, sha)), None)
        out["files"].append({"file": item["file"], "installed": item["installed"], "size": size, "sha256": sha,
                             "already_here_as": already})
        if not item["installed"] and not already:
            if size is None:
                out["size_known"] = False
            else:
                out["download_bytes"] += size
    if out["gated"]:
        out["problem"] = GATED
    return out


def download(data_dir, ref: dict, *, fetcher=fetch_https, fetch_json=get_json, allow_pickle: bool = False,
             on_progress=None) -> dict:
    """Fetch what a reference lists into the shared folder; return its status.

    A file already here at its path is not fetched. A file whose SHA-256 is
    already here under another reference is linked to the same blob. Every
    fetched file is checked against the SHA-256 and size Hugging Face or the
    manifest gives, and refused on a mismatch. Pickle-format files need
    `allow_pickle` (the user's say on the install screen). Raises ModelError.
    """
    if ref["source"] == "ollama":
        raise ModelError(f"{ref['name']}: {OLLAMA_MODELS}.")
    if ref["source"] not in ("huggingface", "url"):
        raise ModelError(f"{ref['name']}: Clips Kitty doesn't download {ref['source']} models")
    pickles = [f for f in ref["files"] if is_pickle(f)]
    if pickles and not allow_pickle:
        raise ModelError(f"{ref['name']}: {', '.join(pickles)} is in a pickle format, which can run code when it "
                         "is loaded. Confirm to download it.")
    info = plan(data_dir, ref, fetch_json=fetch_json)
    if info["gated"]:
        raise ModelError(f"{ref['name']}: {info['problem']}")
    base = root(data_dir)
    tmp_dir = base / "tmp"
    for n, item in enumerate(info["files"]):
        if item["installed"]:
            continue
        target = target_path(data_dir, ref, item["file"])
        if not _inside(target, base):
            raise ModelError(f"{item['file']!r} would land outside the model folder")
        blob_dir = (_repo_dir(data_dir, ref["id"]) / "blobs") if ref["source"] == "huggingface" else None
        with _lock:
            index = load_index(data_dir)
            reuse = None
            if item["sha256"]:
                for _, entry in _by_sha(index, item["sha256"]):
                    blob = Path(entry.get("blob") or entry.get("path") or "")
                    if blob.exists() and _inside(blob, base):
                        reuse = blob
                        break
        if reuse is not None:
            sha, size, blob = item["sha256"], reuse.stat().st_size, reuse
        else:
            tmp_dir.mkdir(parents=True, exist_ok=True)
            part = tmp_dir / f"{secrets.token_hex(8)}.part"
            url = hf_file_url(ref, item["file"]) if ref["source"] == "huggingface" else ref["id"]
            if on_progress:
                on_progress({"file": item["file"], "index": n, "count": len(info["files"])})
            try:
                fetcher(url, part)
                size = part.stat().st_size
                if size > MAX_FILE_BYTES:
                    raise ModelError(f"{item['file']} is larger than {MAX_FILE_BYTES // 1024**3} GB")
                if item["size"] is not None and size != item["size"]:
                    raise ModelError(f"{item['file']}: got {size} bytes, expected {item['size']}")
                sha = _sha256_of(part)
                if item["sha256"] and sha != item["sha256"]:
                    raise ModelError(f"{item['file']}: its SHA-256 doesn't match what "
                                     + ("the manifest" if ref["source"] == "url" else "Hugging Face") + " says")
                blob = (blob_dir / sha) if blob_dir else target
                blob.parent.mkdir(parents=True, exist_ok=True)
                if blob.exists():
                    part.unlink()
                else:
                    os.replace(part, blob)
            except (OSError, urllib.error.URLError) as e:
                log.warning("Couldn't download %s of %s: %s", item["file"], ref["id"], e)
                raise ModelError(f"Couldn't download {item['file']}. Check your internet connection and "
                                 "try again.") from e
            finally:
                discard(part)  # never replaces the error being raised (issue #74)
        if blob == target:
            link = None
        else:
            link = _link(blob, target)
        with _lock:
            index = load_index(data_dir)
            index["files"][_key(ref, item["file"])] = {
                "sha256": sha, "size": size, "path": str(target), "blob": str(blob), "link": link,
                "added": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            _save_index(data_dir, index)
    return status(data_dir, ref)


# ---- what a plugin gets --------------------------------------------------------------------------


def for_job(data_dir, data: dict, *, ollama_host: str | None = None, fetch_json=get_json) -> tuple[dict, list[str]]:
    """job.json's "models" for a plugin: {name: {source, id, path, revision,
    files: {file: path}}}, and a sentence for each model that isn't on this PC,
    saying where to get it. Ollama is asked only when the plugin lists an
    Ollama model."""
    refs = refs_of(data)
    installed = (ollama_models(ollama_host or "http://localhost:11434", fetch_json=fetch_json)
                 if any(r["source"] == "ollama" for r in refs) else None)
    out, missing = {}, []
    for ref in refs:
        st = status(data_dir, ref, ollama=installed)
        if st["installed"] is False:
            model = f"Its AI model '{ref['name']}'"
            if ref["source"] == "ollama":
                missing.append(f"{model} isn't downloaded yet. Download {ref['id']} on the Models page.")
            elif ref["source"] == "bundled":
                missing.append(f"{model} isn't part of this copy of Clips Kitty.")
            else:
                size = f" ({size_text(ref['size_bytes'])})" if ref["size_bytes"] else ""
                missing.append(f"{model} isn't downloaded yet. Open Marketplace › Installed and press "
                               f"Download{size}.")
            continue
        files = {f["file"]: f["path"] for f in st["files_status"] if f["path"]}
        out[ref["name"]] = {"source": ref["source"], "id": ref["id"], "path": st["path"] or "",
                            "revision": ref["revision"] or ref["sha256"] or "", "files": files}
    return out, missing


def overview(data_dir, plugins: list[dict], *, ollama: list[dict] | None = None) -> dict:
    """Every model the installed plugins reference, once, with where it is and
    which plugins use it (GET /plugin-models). `plugins` is [{id, manifest}]."""
    seen: dict[str, dict] = {}
    for plugin in plugins:
        for ref in refs_of(plugin["manifest"]):
            key = "|".join((ref["source"], ref["id"], ref["revision"] or ref["sha256"] or "",
                            ",".join(sorted(ref["files"]))))
            if key not in seen:
                st = status(data_dir, ref, ollama=ollama)
                seen[key] = {**st, "used_by": []}
            seen[key]["used_by"].append({"plugin": plugin["id"], "name": ref["name"]})
    models = list(seen.values())
    index = load_index(data_dir)
    stored = sum(int(v.get("size") or 0) for v in {v.get("blob"): v for v in index["files"].values()}.values())
    return {"models": models, "folder": str(root(data_dir)), "stored_bytes": stored,
            "ollama_reachable": ollama is not None}
