import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui" / "src" / "renderer" / "src"
FILES = (
    "components/GlossaryEditor.tsx",
    "components/WatermarkCard.tsx",
    "pages/Models.tsx",
    "pages/Watch.tsx",
    "pages/Creators.tsx",
    "components/UploadPostCard.tsx",
    "components/ClipUpdatesCard.tsx",
    "components/YouTubeCard.tsx",
    "components/UploadPostPanel.tsx",
    "pages/Settings.tsx",
    "pages/ClipStudio.tsx",
    "components/ClipEditor.tsx",
    "pages/Dashboard.tsx",
    "components/AICard.tsx",
    "components/FeedbackHub.tsx",
    "components/queue/AddVideos.tsx",
    "components/queue/QueueItemSettings.tsx",
    "components/MultilingualExport.tsx",
    "components/ClipCard.tsx",
    "components/ApprovalCard.tsx",
    "components/EditSuggestions.tsx",
    "components/TranslationReview.tsx",
    "components/EditorModal.tsx",
    "components/ExportSettingsCard.tsx",
    "components/ExportProgress.tsx",
)

ATTRIBUTE = re.compile(r"""\b(?:placeholder|title|aria-label)\s*=\s*(['"])(.*?)\1""", re.DOTALL)


def _is_non_translatable_example(value: str) -> bool:
    value = value.strip()
    return (
        value.startswith(("http://", "https://", "#", "@", "e.g."))
        or "/" in value
        or "\\" in value
        or bool(re.fullmatch(r"[A-Z0-9_-]{3,}", value))
    )


def test_user_facing_literal_attributes_use_translation_function():
    untranslated = []
    for relative in FILES:
        source = (UI / relative).read_text(encoding="utf-8")
        for match in ATTRIBUTE.finditer(source):
            value = match.group(2)
            if not _is_non_translatable_example(value):
                untranslated.append(f"{relative}: {value}")
    assert not untranslated, "Hard-coded UI attributes remain:\\n" + "\\n".join(untranslated)
