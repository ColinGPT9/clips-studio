"""#138: the Generate row remembers its settings for the next video. Most
switches already were; the caption style was read but never written, the
Longform output picked from its list was never saved, and the Pipeline and
Rate & understand plugins were forgotten. The row has no React test harness,
so this is checked in its source, as tests/test_ui_steps.py does."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROW = ROOT / "ui" / "src" / "renderer" / "src" / "components" / "queue" / "AddVideos.tsx"


def _source() -> str:
    return ROW.read_text(encoding="utf-8")


def _pref(name: str) -> str:
    match = re.search(rf"\b{name}: '([a-z-]+)'", _source())
    assert match, f"PREF has no {name}"
    return match.group(1)


def test_every_remembered_key_is_written_as_well_as_read():
    text = _source()
    for name in ("caption_style", "longform_mode", "pipeline", "rate", "understand", "edit"):
        key = _pref(name)
        assert key.startswith("generate-")
        assert text.count(f"PREF.{name}") + text.count("PREF[step]") >= 2, name
    # The style a new row starts from is the one last edited.
    assert "localStorage.setItem(PREF.caption_style, JSON.stringify(style))" in text
    assert "localStorage.getItem(PREF.caption_style)" in text


def test_the_longform_output_picked_from_the_list_is_saved():
    text = _source()
    select = text[text.index("{t('Longform output')}"):]
    select = select[:select.index("</select>")]
    assert "localStorage.setItem(PREF.longform_mode, e.target.value)" in select


def test_the_plugins_last_chosen_start_the_next_row():
    text = _source()
    start = text[text.index("function startingOptions"):]
    start = start[:start.index("\n}\n") if "\n}\n" in start else len(start)]
    assert "rememberedChoices(PREF.pipeline)" in start
    assert "rememberedChoices(PREF[step])" in start
    # Not with the modes that pick the moments themselves.
    assert "if (o.longform) return o" in start
    assert "!o.sport && !o.gaming_scoring" in start
    assert "return withoutPipelineInSteps(o)" in start
    # Written where they are chosen.
    assert "rememberChoices(PREF.pipeline, [pipeline])" in text
    assert "rememberChoices(PREF[step], list)" in text


def test_a_remembered_plugin_that_has_gone_is_forgotten_too():
    # Dropped from the rows only, it came back on the row made after Generate,
    # and the server refused it.
    text = _source()
    assert "function forgetGone" in text
    for call in ("forgetGone(PREF.pipeline, pipelines)",
                 "forgetGone(PREF[step], usableFor(stepPlugins, step))",
                 "forgetGone(PREF.edit, editPlugins)"):
        assert call in text, call
