"""Step V1 in the desktop app: Settings → Voice and the dubbing dialog.

The app has no React test harness, so, as tests/test_ui_step4.py does, the
card and the dialog are checked in their source: that they talk to the
routes the engine has (docs/editor-suite/api/stepV1-voice.md), that every
word goes through t(), and that they keep what the user guide says (the
window never talks to a voice app itself, the "not for monetised videos" tag
is shown only for a voice the engine marks as not cleared, a dub sends only
a voice the person picked).
"""

import re
from pathlib import Path

from test_ui_i18n_editor import untranslated

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui" / "src" / "renderer" / "src"


def _source(relative: str) -> str:
    return (UI / relative).read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return " ".join(text.split())


def _engine_ids() -> list[str]:
    """voice/settings.py's ENGINES, read from its source (no import, so this
    runs where the engine's own dependencies are not installed)."""
    source = (ROOT / "voice" / "settings.py").read_text(encoding="utf-8")
    found = re.search(r"^ENGINES = \(([^)]*)\)", source, re.M)
    assert found, "voice/settings.py no longer names its engines"
    return re.findall(r'"(\w+)"', found.group(1))


# ---- Settings → Voice ---------------------------------------------------------------------------

def test_the_card_shows_no_english_that_bypasses_t():
    assert untranslated(_source("components/VoiceCard.tsx")) == []


def test_the_card_sits_right_after_the_ai_card():
    settings = _source("pages/Settings.tsx")
    assert "import VoiceCard from '../components/VoiceCard'" in settings
    assert re.search(r"<AICard [^\n]*/>\n(\s*\{/\*.*?\*/\}\n)?\s*<VoiceCard />", settings, re.S)


def test_the_card_uses_the_voice_routes_with_the_session_header():
    api = _source("lib/api.ts")
    helper = api[api.index("async function voiceRequest"):]
    helper = helper[:helper.index("\n}\n")]
    assert "headers: await sessionHeaders()" in helper
    # A voice error's plain line is what the card shows.
    assert "message" in helper and "detail" in helper

    assert "voice: () => voiceRequest<VoiceStatus>('/voice')" in api
    assert "voiceRequest<VoiceStatus>('/voice/engine', { method: 'POST'" in _flat(api)
    assert "voiceRequest<VoiceList>(`/voice/voices?language=${encodeURIComponent(language)}`)" in _flat(api)
    assert "`/voice/preview?language=${encodeURIComponent(language)}`" in api
    assert "`&voice=${encodeURIComponent(voice)}`" in api
    # The sample is played from the engine's own address (the app's content
    # policy allows media from there only).
    assert "return `${API_BASE}${got.url}`" in api

    card = _source("components/VoiceCard.tsx")
    for call in ("api.voice()", "api.setVoice(", ".voiceList(shown)", "api.voiceSample("):
        assert call in card, call


def test_the_window_never_talks_to_a_voice_app_itself():
    card = _source("components/VoiceCard.tsx")
    assert "fetch(" not in card
    # The only address in the card is the example in the address box.
    assert re.findall(r"https?://[^\s'\"`<]+", card) == ["http://127.0.0.1:8880"]
    for port in ("17493", "3900"):
        assert port not in card
        assert port not in _source("lib/api.ts")
        assert port not in _source("components/MultilingualExport.tsx")


def test_every_engine_has_a_tooltip_and_its_name_goes_through_t():
    card = _source("components/VoiceCard.tsx")
    tips = card[card.index("const TIP"):]
    tips = tips[:tips.index("\n}\n")]
    assert re.findall(r"^  (\w+): '", tips, re.M) == _engine_ids()
    assert "<span>{t(e.name)}</span>" in card
    # The tooltip carries the engine's line on who answers for a voice's
    # licence (VoiceStudio, another local voice app).
    assert "title={[t(TIP[e.id]), e.licence_note && t(e.licence_note)].filter(Boolean).join(' ')}" in card


def test_choosing_an_engine_saves_it_at_once():
    card = _flat(_source("components/VoiceCard.tsx"))
    assert "void run(() => api.setVoice({ engine: e.id }))" in card
    assert "api.setVoice({ voices: { [shown]: e.target.value } })" in card
    assert "api.setVoice({ fallback_to_builtin: e.target.checked })" in card
    assert "api.setVoice({ custom_address: address.trim() })" in card


def test_the_reachability_line_is_the_engines_own_words():
    card = _flat(_source("components/VoiceCard.tsx"))
    assert "const line = list?.message || chosen?.message || ''" in card
    assert "<span>{t(line)}</span>" in card
    # Nothing in the card writes that line itself.
    assert "to use it" not in card.replace('("Open Voicebox to use it.")', "")


def test_the_address_box_and_standing_in_show_only_where_they_apply():
    card = _flat(_source("components/VoiceCard.tsx"))
    assert "{status.engine === 'custom' && (" in card
    before, after = card.split("{t('Use the built-in voice when it is closed')}")
    assert before.rstrip().endswith("<span>")
    assert "{chosen?.optional && ( <label" in before
    assert "checked={status.settings.fallback_to_builtin}" in before


def test_the_tag_shows_only_for_a_voice_marked_not_cleared():
    card = _source("components/VoiceCard.tsx")
    assert card.count("commercial_ok") == 2
    assert card.count("commercial_ok === false") == 2
    assert "{selected?.commercial_ok === false && (" in card
    assert "{t('not for monetised videos')}" in card


def test_a_voice_that_voicebox_has_not_downloaded_cannot_be_picked():
    card = _source("components/VoiceCard.tsx")
    assert "<option key={v.id} value={v.id} disabled={v.ready === false}>" in card


def test_hear_this_voice_plays_what_the_engine_made():
    card = _flat(_source("components/VoiceCard.tsx"))
    assert "setPlayer(await api.voiceSample(shown, list?.default))" in card
    assert "<audio key={player} src={player} controls autoPlay" in card
    assert "▶ {t('Hear this voice')}" in card


def test_no_control_is_disabled_while_a_change_is_saved():
    """A browser drops the focus of a control that becomes disabled, so the
    arrow keys would stop after one step. A change made while another is
    being saved is ignored instead."""
    card = _flat(_source("components/VoiceCard.tsx"))
    assert "disabled={busy" not in card
    assert "aria-busy={busy}" in card
    assert "if (working.current) return false" in card
    # Nothing but "there are no voices" disables the list and the button.
    assert card.count("disabled={voices.length === 0}") == 2
    # Save stays in the tab order when there is nothing to save.
    assert "aria-disabled={unchanged}" in card
    assert "if (!unchanged) void run(" in card


def test_the_app_may_play_sound_from_the_engine():
    page = (ROOT / "ui" / "src" / "renderer" / "index.html").read_text(encoding="utf-8")
    assert "media-src http://127.0.0.1:8765" in page


# ---- the dubbing dialog --------------------------------------------------------------------------

def test_the_old_install_hint_is_gone():
    dialog = _source("components/MultilingualExport.tsx")
    assert "pip install" not in dialog
    assert "piper-tts" not in dialog


def test_the_dialog_shows_the_engines_line_when_the_voice_app_is_closed():
    dialog = _flat(_source("components/MultilingualExport.tsx"))
    assert "[c]: { voices: r.voices, default: r.default, message: r.message ?? '' }" in dialog
    # Once for a closed app, and one line for each language the open app has
    # no voice for.
    assert "new Set(picked.map((c) => voiceList[c]?.message)" in dialog
    assert "{voiceMessages.map((m) => ( <p key={m} className=\"text-xs text-warn\"> {t(m)} </p> ))}" in dialog


def test_the_dialog_preselects_the_default_and_sends_only_a_picked_voice():
    dialog = _flat(_source("components/MultilingualExport.tsx"))
    assert "const shownVoice = (c: string): string => pickedVoice(c) || voiceList[c]?.default || ''" in dialog
    assert "value={shownVoice(c)}" in dialog
    # A dub with nothing picked sends no voice, as it always did: the engine
    # uses the default then.
    assert ("voices: Object.fromEntries(picked.map((c) => [c, pickedVoice(c)]).filter(([, v]) => v))"
            in dialog)
    assert "voices: voiceFor," not in dialog


def test_the_download_note_shows_only_for_a_voice_that_downloads():
    dialog = _flat(_source("components/MultilingualExport.tsx"))
    assert "?.engine === 'piper'" in dialog
    assert ("{downloadsVoice && ( <p className=\"text-muted/70\"> "
            "{t('The first play downloads that voice (~60 MB), so it can take a moment.')}") in dialog


# ---- the setup wizard ----------------------------------------------------------------------------

def test_the_setup_wizard_names_the_voice_check():
    wizard = _source("components/SetupWizard.tsx")
    labels = wizard[wizard.index("const LABEL"):]
    labels = labels[:labels.index("\n}\n")]
    assert "voice: 'Voice'" in labels
    assert 'name="voice"' in (ROOT / "core" / "preflight.py").read_text(encoding="utf-8")
