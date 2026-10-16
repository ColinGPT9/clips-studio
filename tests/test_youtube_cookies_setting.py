"""The YouTube cookies setting (#136) is saved from Settings → Advanced
settings and used by the next download, without a restart."""

import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


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
    settings = tmp_path / "settings.yaml"
    shutil.copy(ROOT / "config" / "settings.yaml", settings)
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(tmp_path / "data")
    app = create_app(config, settings)
    return TestClient(app, base_url="http://127.0.0.1"), config, settings, yaml


def test_off_by_default(api):
    client, _config, _settings, _yaml = api
    assert client.get("/settings").json()["youtube_cookies"] == ""


def test_a_cookies_file_is_saved_and_used_at_once(api, tmp_path):
    from sources import ytdlp_common

    client, config, settings, yaml = api
    jar = tmp_path / "my cookies" / "cookies.txt"
    jar.parent.mkdir()
    jar.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    assert client.patch("/settings", json={"youtube_cookies": f'"{jar}"'}).status_code == 200
    assert yaml.safe_load(settings.read_text(encoding="utf-8"))["youtube_cookies"] == str(jar)
    assert config["youtube_cookies"] == str(jar) and ytdlp_common.cookies() == str(jar)
    assert client.get("/settings").json()["youtube_cookies"] == str(jar)
    # Set again, the line is replaced, not added twice.
    assert client.patch("/settings", json={"youtube_cookies": "Firefox"}).status_code == 200
    text = settings.read_text(encoding="utf-8")
    assert text.count("youtube_cookies:") == 1 and yaml.safe_load(text)["youtube_cookies"] == "firefox"
    assert client.patch("/settings", json={"youtube_cookies": ""}).status_code == 200
    assert ytdlp_common.cookies() == ""


def test_a_file_that_is_not_there_is_refused(api, tmp_path):
    client, _config, settings, _yaml = api
    before = settings.read_text(encoding="utf-8")
    assert client.patch("/settings", json={"youtube_cookies": str(tmp_path / "none.txt")}).status_code == 400
    assert settings.read_text(encoding="utf-8") == before
