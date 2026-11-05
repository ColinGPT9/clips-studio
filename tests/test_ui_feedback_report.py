"""A bug report describes the video the reporter picked (#213).

The Feedback Hub's preview asked the engine for the chosen video's
diagnostics, but Send left the video out, so every report described whichever
video was processed last: #213 named "дверистрим" and arrived with a deleted
Twitch VOD's details instead.
"""

import re
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "ui" / "src" / "renderer" / "src"


def test_send_passes_the_picked_video_like_the_preview():
    hub = (UI / "components" / "FeedbackHub.tsx").read_text(encoding="utf-8")
    send = hub[hub.index("api.feedbackSubmit({"):]
    send = send[: send.index("images:")]
    assert "video_id: realVideoId()" in send
    assert "api.feedbackDiagnostics(realVideoId())" in hub
    api = (UI / "lib" / "api.ts").read_text(encoding="utf-8")
    assert re.search(r"feedbackSubmit: \(payload: \{[^}]*video_id\?: string", api)
