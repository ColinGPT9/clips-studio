"""The Sports row's Crop in the app (ui/src/renderer/src/lib/sports.ts): basketball's Full screen
is kept and remembered for the next video, the default isn't written, and a sport without the
choice never carries it. Run under Node; skipped without it."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent / "ui" / "src" / "renderer" / "src" / "lib"
SPORTS = [
    {"id": "soccer", "label": "Soccer", "highlights": [{"id": "best", "label": "Best moments"}], "periods": []},
    {"id": "basketball", "label": "Basketball", "highlights": [{"id": "best", "label": "Best moments"}],
     "periods": [], "screens": [{"id": "safe", "label": "Clear of app text"}, {"id": "full", "label": "Full screen"}]},
]


def _run(tmp_path, body: str):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node isn't available")
    # sports.ts with React and the API client stood in for: only its plain functions run here.
    text = (LIB / "sports.ts").read_text(encoding="utf-8")
    text = text.replace("from 'react'", "from './react_stub.ts'").replace("from './api'", "from './api_stub.ts'")
    (tmp_path / "sports.ts").write_text(text, encoding="utf-8")
    (tmp_path / "react_stub.ts").write_text(
        "export const useEffect = () => {}\nexport const useState = (v: unknown) => [v, () => {}]\n", encoding="utf-8")
    (tmp_path / "api_stub.ts").write_text("export const api = {}\n", encoding="utf-8")
    script = ("const store = new Map();"
              "globalThis.localStorage = {getItem: k => store.has(k) ? store.get(k) : null,"
              " setItem: (k, v) => store.set(k, String(v))};"
              f"const m = await import({json.dumps((tmp_path / 'sports.ts').as_uri())});"
              f"const list = {json.dumps(SPORTS)};"
              f"console.log(JSON.stringify(({body})(m, list)));")
    r = subprocess.run([node, "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0 and "strip-types" in r.stderr:
        pytest.skip("this Node can't run TypeScript directly")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_full_screen_is_kept_only_where_it_is_offered_and_the_default_is_not_written(tmp_path):
    got = _run(tmp_path, """(m, list) => [
        m.fitSport({name: 'basketball', screen: 'full'}, list),
        m.fitSport({name: 'basketball', screen: 'safe'}, list),
        m.fitSport({name: 'basketball', screen: 'letterbox'}, list),
        m.fitSport({name: 'soccer', screen: 'full'}, list),
    ]""")
    assert got[0]["screen"] == "full"
    assert all("screen" not in o for o in got[1:])


def test_the_crop_is_remembered_for_the_next_video_and_named_on_the_queue_chip(tmp_path):
    got = _run(tmp_path, """(m, list) => {
        m.rememberSport({name: 'basketball', highlights: 'best', screen: 'full'})
        const last = m.lastSport()
        return [last, m.startingSport(list), m.describeSport({name: 'basketball', screen: 'full'}),
                m.describeSport({name: 'basketball'})]
    }""")
    last, starting, chip, plain = got
    assert last["screen"] == "full" and starting["name"] == "basketball" and starting["screen"] == "full"
    assert chip.endswith("full") and "full" not in plain.lower().split(" · ")[-1]
