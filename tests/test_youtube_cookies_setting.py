"""The YouTube cookies setting (#136) is saved from Settings → Advanced
settings and used by the next download, without a restart. A cookies.txt
arrives as its text and is kept in the data folder: the setting never holds a
path, so nothing the request says decides which file is read or written."""

import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
JAR = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tx\n"


@pytest.fixture
def api(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    yaml = pytest.importorskip("yaml")
    from fastapi.testclient import TestClient

    from main import BUNDLED_CONFIG, load_config
    from server.api import create_app
    from sources import ytdlp_common

    monkeypatch.setattr(ytdlp_common, "_cookies", "")
    monkeypatch.setattr(ytdlp_common, "_cookie_file", None)
    settings = tmp_path / "settings.yaml"
    shutil.copy(ROOT / "config" / "settings.yaml", settings)
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(tmp_path / "data")
    app = create_app(config, settings)
    return TestClient(app, base_url="http://127.0.0.1"), config, settings, yaml


def test_off_by_default(api):
    client, _config, _settings, _yaml = api
    assert client.get("/settings").json()["youtube_cookies"] == ""


def test_a_cookies_file_is_kept_in_the_data_folder_and_used_at_once(api, tmp_path):
    from sources import ytdlp_common

    client, config, settings, yaml = api
    assert client.patch("/settings", json={"youtube_cookies_text": JAR}).status_code == 200
    jar = tmp_path / "data" / ytdlp_common.COOKIES_FILE
    assert jar.read_text(encoding="utf-8") == JAR
    assert yaml.safe_load(settings.read_text(encoding="utf-8"))["youtube_cookies"] == "file"
    assert config["youtube_cookies"] == "file" and ytdlp_common.cookies() == "file"
    assert ytdlp_common._cookie_file == jar
    assert client.get("/settings").json()["youtube_cookies"] == "file"
    # Set again, the line is replaced, not added twice; and the sign-in isn't kept.
    assert client.patch("/settings", json={"youtube_cookies": "Firefox"}).status_code == 200
    text = settings.read_text(encoding="utf-8")
    assert text.count("youtube_cookies:") == 1 and yaml.safe_load(text)["youtube_cookies"] == "firefox"
    assert not jar.exists()
    assert client.patch("/settings", json={"youtube_cookies": ""}).status_code == 200
    assert ytdlp_common.cookies() == ""


def test_the_file_is_kept_byte_for_byte(api, tmp_path):
    client, _config, _settings, _yaml = api
    crlf = JAR.replace("\n", "\r\n")                     # as a Windows exporter writes it
    assert client.patch("/settings", json={"youtube_cookies_text": crlf}).status_code == 200
    assert (tmp_path / "data" / "youtube_cookies.txt").read_bytes() == crlf.encode("utf-8")


@pytest.mark.parametrize("body", [
    {"youtube_cookies_text": "SID=x; HSID=y"},             # a Cookie header, not a cookies.txt
    {"youtube_cookies": "C:/Users/me/cookies.txt"},        # a path is not a setting
    {"youtube_cookies": "chrome"},
])
def test_anything_else_is_refused_and_nothing_is_saved(api, tmp_path, body):
    client, _config, settings, _yaml = api
    before = settings.read_text(encoding="utf-8")
    assert client.patch("/settings", json=body).status_code == 400
    assert settings.read_text(encoding="utf-8") == before
    assert not (tmp_path / "data" / "youtube_cookies.txt").exists()
