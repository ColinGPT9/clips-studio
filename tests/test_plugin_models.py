"""Model references and the shared model folder (plugins/models.py).

No network: Hugging Face's metadata and files, Ollama's model list and the
app's bundled models are all stand-ins, and every download goes through a
fake fetcher that records what it was asked for. The sizes are bytes, not
gigabytes.
"""

import errno
import hashlib
import http.client
import os
import urllib.error
from pathlib import Path

import pytest

from plugins import models

REV = "a" * 40
REV2 = "b" * 40
CONFIG = b'{"labels": ["kill", "team_wipe"]}'
WEIGHTS = b"onnx-weights-" * 100


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Hub:
    """Hugging Face as far as plugins/models.py sees it: model info, the file
    tree at a commit, and the files. Records every download."""

    def __init__(self, files: dict, *, license="apache-2.0", gated=False, lfs=True):
        self.files = files  # {path: bytes}
        self.license, self.gated, self.lfs = license, gated, lfs
        self.downloads: list[str] = []
        self.json_calls: list[str] = []

    def fetch_json(self, url):
        self.json_calls.append(url)
        if "/revision/" in url:
            return {"id": "example-org/example-model", "sha": REV, "gated": self.gated and "manual",
                    "cardData": {"license": self.license}}
        if "/tree/" in url:
            return [{"type": "file", "path": p, "size": len(b),
                     **({"lfs": {"oid": sha(b), "size": len(b)}} if self.lfs else {})}
                    for p, b in self.files.items()]
        raise OSError(f"unexpected {url}")

    def fetcher(self, url, dest):
        self.downloads.append(url)
        for path, data in self.files.items():
            if url.endswith("/" + path) or url.endswith(path):
                Path(dest).write_bytes(data)
                return
        raise OSError(f"404 {url}")


def hf(name="killfeed", files=("config.json", "model.onnx"), revision=REV, **extra) -> dict:
    return models.parse({"name": name, "source": "huggingface", "id": "example-org/example-model",
                         "revision": revision, "files": list(files), **extra})


# ---- references ------------------------------------------------------------------------------


def test_each_source_parses_to_a_reference():
    refs = models.refs_of({"models": [
        {"name": "killfeed", "source": "huggingface", "id": "example-org/example-model", "revision": REV.upper(),
         "files": ["model.onnx"], "license": "mit"},
        {"name": "weights", "source": "url", "id": "https://example.com/dl/weights v2.onnx?x=1", "sha256": "c" * 64},
        {"name": "chat", "source": "ollama", "id": "example-model:latest"},
        {"name": "speech", "source": "bundled", "id": "whisper:small"},
        {"name": "broken", "source": "nowhere"},
    ]})
    assert [r["name"] for r in refs] == ["killfeed", "weights", "chat", "speech"]
    assert refs[0]["revision"] == REV and refs[0]["license"] == "mit"
    assert refs[1]["files"] == ["weights_v2.onnx"]


def test_a_url_models_file_name_keeps_what_the_pickle_check_reads():
    """The download asks before a pickle-format file by its saved name; a
    long name is shortened but keeps its extension, and a #fragment is not
    part of it."""
    def name(url):
        return models.parse({"name": "w", "source": "url", "id": url, "sha256": "c" * 64})["files"][0]

    long = name("https://example.com/dl/" + "m" * 130 + ".ckpt")
    assert len(long) == 120 and long.endswith(".ckpt") and models.is_pickle(long)
    assert name("https://example.com/dl/model.pt#v1") == "model.pt"
    assert name("https://example.com/dl/" + "m" * 130 + ".onnx").endswith(".onnx")
    assert name("https://example.com/dl/..") == "model"  # never a folder above its own
    assert name("https://example.com/") == "model"


def test_paths_stay_inside_the_shared_folder(tmp_path):
    with pytest.raises(models.ModelError, match="inside the model"):
        models.target_path(tmp_path, hf(), "../outside.onnx")
    with pytest.raises(models.ModelError, match="40-character"):
        models.target_path(tmp_path, hf(revision="main"), "model.onnx")
    path = models.target_path(tmp_path, hf(), "sub/model.onnx")
    assert path == (tmp_path / "plugin-models" / "huggingface" / "models--example-org--example-model"
                    / "snapshots" / REV / "sub" / "model.onnx")


# ---- downloading once, sharing everywhere -----------------------------------------------------------


def test_a_download_lands_in_the_hugging_face_layout_and_reports_itself(tmp_path):
    hub = Hub({"config.json": CONFIG, "model.onnx": WEIGHTS})
    ref = hf()
    before = models.status(tmp_path, ref)
    assert before["installed"] is False and before["path"] is None

    plan = models.plan(tmp_path, ref, fetch_json=hub.fetch_json)
    assert plan["license"] == "apache-2.0" and plan["download_bytes"] == len(CONFIG) + len(WEIGHTS)
    assert plan["size_known"] and not plan["problem"]
    assert hub.downloads == []  # a plan downloads nothing

    st = models.download(tmp_path, ref, fetcher=hub.fetcher, fetch_json=hub.fetch_json)
    assert st["installed"] is True and st["size_bytes"] == len(CONFIG) + len(WEIGHTS)
    snapshot = Path(st["path"])
    assert snapshot.name == REV and (snapshot / "model.onnx").read_bytes() == WEIGHTS
    blobs = snapshot.parent.parent / "blobs"
    assert sorted(p.name for p in blobs.iterdir()) == sorted([sha(CONFIG), sha(WEIGHTS)])
    assert hub.downloads == [f"https://huggingface.co/example-org/example-model/resolve/{REV}/config.json",
                             f"https://huggingface.co/example-org/example-model/resolve/{REV}/model.onnx"]
    assert st["link"] in ("symbolic link", "hard link", "copy")
    assert not any((tmp_path / "plugin-models" / "tmp").iterdir())


def test_two_plugins_naming_the_same_model_share_one_download(tmp_path):
    hub = Hub({"config.json": CONFIG, "model.onnx": WEIGHTS})
    first = models.download(tmp_path, hf(name="killfeed"), fetcher=hub.fetcher, fetch_json=hub.fetch_json)
    count = len(hub.downloads)
    second = models.download(tmp_path, hf(name="detector"), fetcher=hub.fetcher, fetch_json=hub.fetch_json)
    assert len(hub.downloads) == count  # nothing fetched the second time
    assert second["path"] == first["path"]

    overview = models.overview(tmp_path, [
        {"id": "example-dev/plugin-a", "manifest": {"models": [{"name": "killfeed", "source": "huggingface",
                                                                 "id": "example-org/example-model", "revision": REV,
                                                                 "files": ["config.json", "model.onnx"]}]}},
        {"id": "example-dev/plugin-b", "manifest": {"models": [{"name": "detector", "source": "huggingface",
                                                                 "id": "example-org/example-model", "revision": REV,
                                                                 "files": ["model.onnx", "config.json"]}]}}])
    (entry,) = overview["models"]
    assert entry["installed"] is True
    assert [u["plugin"] for u in entry["used_by"]] == ["example-dev/plugin-a", "example-dev/plugin-b"]
    assert overview["stored_bytes"] == len(CONFIG) + len(WEIGHTS)


def test_a_file_already_here_under_another_reference_is_linked_not_fetched(tmp_path):
    hub = Hub({"config.json": CONFIG, "model.onnx": WEIGHTS})
    models.download(tmp_path, hf(), fetcher=hub.fetcher, fetch_json=hub.fetch_json)
    # The same weights at a newer commit of the model, and as a plain url model.
    newer = hf(files=("model.onnx",), revision=REV2)
    url = models.parse({"name": "weights", "source": "url", "id": "https://example.com/model.onnx",
                        "sha256": sha(WEIGHTS)})
    count = len(hub.downloads)
    plan = models.plan(tmp_path, url, fetch_json=hub.fetch_json)
    assert plan["files"][0]["already_here_as"] and plan["download_bytes"] == 0
    st_new = models.download(tmp_path, newer, fetcher=hub.fetcher, fetch_json=hub.fetch_json)
    st_url = models.download(tmp_path, url, fetcher=hub.fetcher, fetch_json=hub.fetch_json)
    assert len(hub.downloads) == count
    assert Path(st_new["files_status"][0]["path"]).read_bytes() == WEIGHTS
    assert Path(st_url["files_status"][0]["path"]).read_bytes() == WEIGHTS
    assert st_url["shared_with"]  # reported as the same file as the Hugging Face copies


def test_a_file_that_does_not_match_its_checksum_or_size_is_refused(tmp_path):
    hub = Hub({"model.onnx": WEIGHTS})
    url = models.parse({"name": "weights", "source": "url", "id": "https://example.com/model.onnx",
                        "sha256": "d" * 64})
    with pytest.raises(models.ModelError, match="SHA-256 doesn't match what the manifest says"):
        models.download(tmp_path, url, fetcher=hub.fetcher, fetch_json=hub.fetch_json)
    assert models.status(tmp_path, url)["installed"] is False

    class Truncating(Hub):
        def fetcher(self, url, dest):
            super().fetcher(url, dest)
            Path(dest).write_bytes(Path(dest).read_bytes()[:10])

    with pytest.raises(models.ModelError, match="expected"):
        models.download(tmp_path, hf(files=("model.onnx",)), fetcher=Truncating({"model.onnx": WEIGHTS}).fetcher,
                        fetch_json=hub.fetch_json)
    assert models.status(tmp_path, hf(files=("model.onnx",)))["installed"] is False
    assert not any((tmp_path / "plugin-models" / "tmp").iterdir())


def test_without_lfs_metadata_a_file_is_still_hashed_and_stored_once(tmp_path):
    hub = Hub({"config.json": CONFIG}, lfs=False)
    st = models.download(tmp_path, hf(files=("config.json",)), fetcher=hub.fetcher, fetch_json=hub.fetch_json)
    index = models.load_index(tmp_path)
    (entry,) = index["files"].values()
    assert entry["sha256"] == sha(CONFIG) and entry["size"] == len(CONFIG)
    assert Path(st["files_status"][0]["path"]).read_bytes() == CONFIG


def test_pickle_files_need_the_users_say(tmp_path):
    hub = Hub({"model.pt": WEIGHTS})
    ref = hf(files=("model.pt",))
    assert models.status(tmp_path, ref)["pickle_files"] == ["model.pt"]
    with pytest.raises(models.ModelError, match="pickle format"):
        models.download(tmp_path, ref, fetcher=hub.fetcher, fetch_json=hub.fetch_json)
    assert hub.downloads == []
    assert models.download(tmp_path, ref, fetcher=hub.fetcher, fetch_json=hub.fetch_json,
                           allow_pickle=True)["installed"] is True


def test_a_gated_model_is_explained_not_attempted(tmp_path):
    hub = Hub({"model.onnx": WEIGHTS}, gated=True)
    ref = hf(files=("model.onnx",))
    plan = models.plan(tmp_path, ref, fetch_json=hub.fetch_json)
    assert plan["gated"] and plan["problem"] == models.GATED
    with pytest.raises(models.ModelError, match="sign in to Hugging Face"):
        models.download(tmp_path, ref, fetcher=hub.fetcher, fetch_json=hub.fetch_json)
    assert hub.downloads == []


def test_offline_the_plan_says_the_size_is_unknown(tmp_path):
    def offline(url):
        raise OSError("no network")

    plan = models.plan(tmp_path, hf(), fetch_json=offline)
    assert plan["size_known"] is False and plan["license"] is None and not plan["problem"]


def test_a_failed_download_says_so_plainly_and_logs_why(tmp_path, caplog):
    def offline(url, dest):
        raise urllib.error.URLError(OSError(errno.ENETUNREACH, "Network is unreachable: C:/Users/someone/AppData"))

    ref = hf(files=("model.onnx",))
    hub = Hub({"model.onnx": WEIGHTS})
    with pytest.raises(models.ModelError) as e:
        models.download(tmp_path, ref, fetcher=offline, fetch_json=hub.fetch_json)
    assert str(e.value) == "Couldn't download model.onnx. Check your internet connection and try again."
    assert "Network is unreachable" in caplog.text  # the details are in the log, not the message


TRY_AGAIN = "Try again; if it happens again, send a bug report from Feedback (it includes the details)."


@pytest.mark.parametrize("error, said", [
    (urllib.error.HTTPError("https://example.com/model.onnx", 404, "Not Found", {}, None),
     "Couldn't download model.onnx: it isn't at its address any more. Ask the pipeline's developer."),
    (urllib.error.HTTPError("https://example.com/model.onnx", 403, "Forbidden", {}, None),
     "Couldn't download model.onnx: it isn't at its address any more. Ask the pipeline's developer."),
    (urllib.error.HTTPError("https://example.com/model.onnx", 502, "Bad Gateway", {}, None),
     f"Couldn't download model.onnx. {TRY_AGAIN}"),
    (http.client.IncompleteRead(b"x", 100),  # not an OSError
     "Couldn't download model.onnx. Check your internet connection and try again."),
    (TimeoutError("The read operation timed out"),
     "Couldn't download model.onnx. Check your internet connection and try again."),
    (OSError(errno.ENOSPC, "No space left on device"),  # writing the .part file; models can be many GB
     "Couldn't save model.onnx: this PC's disk is full (the file is 1 KB). Free up some space and try again."),
    (PermissionError(errno.EACCES, "Permission denied"),
     f"Couldn't save model.onnx in Clips Kitty's model folder. {TRY_AGAIN}"),
    (OSError(errno.EIO, "Input/output error"), f"Couldn't download model.onnx. {TRY_AGAIN}"),
])
def test_each_kind_of_failed_download_says_its_own_cause(tmp_path, caplog, error, said):
    def failing(url, dest):
        raise error

    hub = Hub({"model.onnx": WEIGHTS})
    with pytest.raises(models.ModelError) as e:
        models.download(tmp_path, hf(files=("model.onnx",)), fetcher=failing, fetch_json=hub.fetch_json)
    assert str(e.value) == said
    assert said.endswith("Check your internet connection and try again.") or "internet" not in said
    assert caplog.text
    assert not any((tmp_path / "plugin-models" / "tmp").iterdir())


def test_a_full_disk_after_the_download_names_the_size_it_knows(tmp_path, monkeypatch):
    def full(*_a, **_k):
        raise OSError(errno.ENOSPC, "No space left on device")

    def no_metadata(url):
        raise OSError("offline")

    hub = Hub({"model.onnx": WEIGHTS})
    monkeypatch.setattr(models.os, "replace", full)
    # Hugging Face's sizes unread: the manifest's size of the whole model is what is known.
    ref = hf(files=("model.onnx",), size_bytes=1_400_000_000)
    with pytest.raises(models.ModelError) as e:
        models.download(tmp_path, ref, fetcher=hub.fetcher, fetch_json=no_metadata)
    assert str(e.value) == ("Couldn't save model.onnx: this PC's disk is full (the model is 1.4 GB). Free up some "
                            "space and try again.")
    with pytest.raises(models.ModelError) as e:
        models.download(tmp_path, hf(files=("model.onnx",)), fetcher=hub.fetcher, fetch_json=no_metadata)
    assert str(e.value) == "Couldn't save model.onnx: this PC's disk is full. Free up some space and try again."

    def denied(*_a, **_k):
        raise PermissionError(errno.EACCES, "Access is denied")

    monkeypatch.setattr(models.os, "replace", denied)
    with pytest.raises(models.ModelError) as e:
        models.download(tmp_path, ref, fetcher=hub.fetcher, fetch_json=hub.fetch_json)
    assert str(e.value) == f"Couldn't save model.onnx in Clips Kitty's model folder. {TRY_AGAIN}"
    assert models.status(tmp_path, ref)["installed"] is False


@pytest.mark.parametrize("fail", [["symlink"], ["symlink", "link"]])
def test_without_symbolic_links_it_falls_back_and_says_how(tmp_path, monkeypatch, fail):
    def refuse(*_a, **_k):
        raise OSError("A required privilege is not held by the client")

    for name in fail:
        monkeypatch.setattr(os, name, refuse)
    hub = Hub({"model.onnx": WEIGHTS})
    st = models.download(tmp_path, hf(files=("model.onnx",)), fetcher=hub.fetcher, fetch_json=hub.fetch_json)
    assert st["link"] == ("hard link" if fail == ["symlink"] else "copy")
    assert Path(st["files_status"][0]["path"]).read_bytes() == WEIGHTS


# ---- Ollama and the app's own models ------------------------------------------------------------------


def test_ollama_models_are_read_from_ollama_not_copied(tmp_path):
    tags = {"models": [{"name": "example-model:latest", "digest": "sha256:" + "e" * 64, "size": 1234},
                       {"name": "other:7b", "digest": "f" * 64}]}
    listed = models.ollama_models("http://localhost:11434", fetch_json=lambda url: tags)
    for model_id, digest, expected in [("example-model", None, True), ("example-model:latest", "e" * 12, True),
                                       ("example-model:latest", "0" * 12, False), ("other:7b", None, True),
                                       ("missing:1b", None, False)]:
        ref = models.parse({"name": "chat", "source": "ollama", "id": model_id, "revision": digest})
        st = models.status(tmp_path, ref, ollama=listed)
        assert st["installed"] is expected, model_id
    assert models.status(tmp_path, ref, ollama=None)["installed"] is None

    def down(url):
        raise OSError("connection refused")

    assert models.ollama_models("http://localhost:11434", fetch_json=down) is None
    with pytest.raises(models.ModelError, match="downloaded on the Models page"):
        models.download(tmp_path, ref)


def test_bundled_models_come_from_the_app(tmp_path):
    ref = models.parse({"name": "speech", "source": "bundled", "id": "whisper:small"})
    found = models.status(tmp_path, ref, bundled=lambda model_id: "/app/vendor/whisper/small")
    assert found["installed"] is True and found["path"] == "/app/vendor/whisper/small"
    missing = models.status(tmp_path, ref, bundled=lambda model_id: None)
    assert missing["installed"] is False and "doesn't carry" in missing["note"]
    unknown = models.parse({"name": "x", "source": "bundled", "id": "example-bundled-model"})
    assert "not a model" in models.status(tmp_path, unknown, bundled=lambda m: None)["note"]


# ---- what a plugin is handed --------------------------------------------------------------------------


def test_a_plugin_gets_paths_for_what_is_here_and_a_reason_for_what_is_not(tmp_path):
    hub = Hub({"model.onnx": WEIGHTS})
    models.download(tmp_path, hf(files=("model.onnx",)), fetcher=hub.fetcher, fetch_json=hub.fetch_json)
    data = {"models": [
        {"name": "killfeed", "source": "huggingface", "id": "example-org/example-model", "revision": REV,
         "files": ["model.onnx"]},
        {"name": "weights", "source": "url", "id": "https://example.com/w.onnx", "sha256": "c" * 64,
         "size_bytes": 52_428_800},
        {"name": "chat", "source": "ollama", "id": "example-model:latest"},
        {"name": "helper", "source": "ollama", "id": "example-helper:7b"}]}
    tags = {"models": [{"name": "example-model:latest"}]}
    handed, missing = models.for_job(tmp_path, data, ollama_host="http://localhost:11434",
                                     fetch_json=lambda url: tags)
    assert set(handed) == {"killfeed", "chat"}
    assert Path(handed["killfeed"]["files"]["model.onnx"]).read_bytes() == WEIGHTS
    assert handed["killfeed"]["revision"] == REV and handed["chat"]["path"] == "example-model:latest"
    assert missing == [("Its AI model 'weights' isn't downloaded yet. Open Marketplace › Installed and press "
                        "Download (52 MB)."),
                       "Its AI model 'helper' isn't downloaded yet. Download example-helper:7b on the Models page."]
    assert models.for_job(tmp_path, {}) == ({}, [])


def test_a_gated_model_that_isnt_here_is_not_sent_to_a_download_button_it_doesnt_have(tmp_path):
    """Marketplace › Installed has no Download button for a gated model
    (plan()'s problem is GATED), so the job doesn't send the person there."""
    data = {"models": [
        {"name": "faces", "source": "huggingface", "id": "example-org/example-gated", "revision": REV,
         "files": ["model.onnx"], "gated": True, "size_bytes": 1_400_000_000},
        {"name": "weights", "source": "url", "id": "https://example.com/w.onnx", "sha256": "c" * 64, "gated": True}]}
    handed, missing = models.for_job(tmp_path, data)
    assert handed == {}
    assert missing == ["Its AI model 'faces' is shared only with people its makers give access to on Hugging Face, "
                       "so Clips Kitty can't download it for you yet.",
                       "Its AI model 'weights' is shared only with people its makers give access to, so Clips Kitty "
                       "can't download it for you yet."]
    assert not any("Download" in line for line in missing)


# ---- the network rules ---------------------------------------------------------------------------------


def test_only_https_leaves_this_pc(tmp_path):
    with pytest.raises(models.ModelError, match="https only"):
        models.get_json("http://example.com/api/models/x")
    with pytest.raises(models.ModelError, match="https only"):
        models.fetch_https("http://example.com/model.onnx", tmp_path / "x")
    handler = models._HttpsOnly()
    with pytest.raises(Exception, match="refused a redirect away from https"):
        handler.redirect_request(None, None, 302, "Found", {}, "http://cdn.example.com/model.onnx")


def test_the_hugging_face_file_address_is_the_resolve_endpoint_at_the_commit():
    assert models.hf_file_url(hf(), "onnx/model v2.onnx") == (
        f"https://huggingface.co/example-org/example-model/resolve/{REV}/onnx/model%20v2.onnx")
