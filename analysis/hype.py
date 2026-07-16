"""Audience hype signals fetched WITHOUT platform API keys.

Two sources, both optional and failure-safe (processing works identically
without them — this only ever ADDS a small scoring bonus):

- Twitch VODs: the public web GQL endpoint every chat-replay tool uses.
  The Client-ID is scraped from twitch.tv itself at call time, because
  Twitch rotates it (hardcoded IDs from 2023-era tools now 400).
- YouTube: the "most replayed" heatmap yt-dlp exposes for popular videos,
  and chat replay (live_chat) for finished live streams.
- Kick: NOT possible — Kick deletes chat when a stream ends and has no
  replay endpoint (verified 2026-07); Kick hype comes from audio/visual
  signals only.

Gift-sub / bits resistance: the curve counts UNIQUE CHATTERS per time bin,
not messages. A gifted-sub bomb produces a burst of messages from few
humans (plus bots), which barely moves unique-chatter density — so fake
"hype" from sub trains doesn't outrank a genuinely popping chat. The bonus
is also hard-capped in fusion so transcript/visual context stays dominant.
"""

import json
import re
import time
import urllib.request
from pathlib import Path

import numpy as np

BIN_SECONDS = 10
_GQL_URL = "https://gql.twitch.tv/gql"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    " (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
_MAX_PAGES = 800          # ~48k messages — covers long VODs
_TIME_BUDGET_S = 180      # never stall the pipeline on a slow chat fetch


def audience_curve(url: str, video_id: str, duration: float) -> np.ndarray | None:
    """Per-second 0..1 hype curve for this video, or None when the platform
    has no fetchable audience data. Never raises."""
    try:
        if video_id.startswith("tw_"):
            times = _twitch_chat(video_id[3:], duration)
            return _chatters_to_curve(times, duration)
        if video_id.startswith("kick_"):
            return None  # Kick retains no chat after the stream ends
        # YouTube: prefer the most-replayed heatmap; fall back to live-chat
        # replay for finished live streams.
        heat = _youtube_heatmap(url, duration)
        if heat is not None:
            return heat
        times = _youtube_live_chat(url, video_id, duration)
        return _chatters_to_curve(times, duration)
    except Exception as e:
        print(f"      (audience signal unavailable: {e})")
        return None


# ---- Twitch ----------------------------------------------------------------


def _scrape_client_id() -> str:
    req = urllib.request.Request("https://www.twitch.tv/", headers={"User-Agent": _UA})
    html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace")
    m = re.search(r'clientId\s*[=:]\s*"([a-z0-9]{20,40})"', html)
    if not m:
        raise RuntimeError("could not find Twitch web client id")
    return m.group(1)


_COMMENTS_QUERY = """
query($videoID: ID!, $offset: Int) {
  video(id: $videoID) {
    comments(contentOffsetSeconds: $offset) {
      edges { node { contentOffsetSeconds commenter { id } } }
      pageInfo { hasNextPage }
    }
  }
}"""


def _twitch_chat(vod_id: str, duration: float) -> list[tuple[float, str]]:
    """(offset_seconds, chatter_id) for the whole VOD's chat replay.

    Paged by contentOffsetSeconds, NOT cursors: cursor pagination trips
    Twitch's client-integrity check (2026), while offset queries — 'give me
    the chat page at second X' — pass. Each page spans until its last
    message; stepping to last+1 walks the whole VOD. Overlaps are deduped.
    """
    client_id = _scrape_client_id()
    seen: set[tuple[float, str]] = set()
    offset = 0.0
    deadline = time.monotonic() + _TIME_BUDGET_S
    for _ in range(_MAX_PAGES):
        body = json.dumps(
            {"query": _COMMENTS_QUERY, "variables": {"videoID": vod_id, "offset": int(offset)}}
        ).encode()
        req = urllib.request.Request(
            _GQL_URL,
            data=body,
            headers={
                "Client-ID": client_id,
                "Content-Type": "application/json",
                "User-Agent": _UA,
            },
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.load(r)
        video = (data.get("data") or {}).get("video")
        if not video or not video.get("comments"):
            break
        edges = video["comments"]["edges"]
        if not edges:
            break
        last = 0.0
        for e in edges:
            node = e["node"]
            commenter = node.get("commenter") or {}
            last = max(last, float(node["contentOffsetSeconds"]))
            # Skip deleted/anonymous accounts — also drops most bot noise.
            if commenter.get("id"):
                seen.add((float(node["contentOffsetSeconds"]), commenter["id"]))
        if last >= duration or last < offset + 1:  # done, or no forward progress
            break
        offset = last + 1
        if time.monotonic() > deadline:
            print("      (chat fetch time budget hit — using the part fetched)")
            break
    return sorted(seen)


# ---- YouTube ----------------------------------------------------------------


def _youtube_heatmap(url: str, duration: float) -> np.ndarray | None:
    """YouTube's own "most replayed" watch-time heatmap (popular videos only),
    resampled to per-second 0..1. This is real audience retention data."""
    import yt_dlp

    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    heat = info.get("heatmap")
    if not heat:
        return None
    n = max(int(duration) + 1, 2)
    curve = np.zeros(n, dtype=np.float32)
    for seg in heat:
        lo = int(float(seg.get("start_time", 0)))
        hi = min(n, int(float(seg.get("end_time", lo + 1))) + 1)
        curve[lo:hi] = float(seg.get("value", 0))
    peak = curve.max()
    if peak <= 0:
        return None
    print(f"      YouTube most-replayed heatmap loaded ({len(heat)} bins)")
    return curve / peak


def _youtube_live_chat(url: str, video_id: str, duration: float) -> list[tuple[float, str]]:
    """Chat replay of a finished YouTube live stream via yt-dlp's live_chat
    subtitle track. Regular uploads have no such track -> empty."""
    import tempfile

    import yt_dlp

    with tempfile.TemporaryDirectory() as td:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "writesubtitles": True,
            "subtitleslangs": ["live_chat"],
            "outtmpl": str(Path(td) / "chat.%(ext)s"),
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
        if "live_chat" not in (info.get("subtitles") or {}):
            return []
        files = list(Path(td).glob("*.live_chat.json"))
        if not files:
            return []
        out: list[tuple[float, str]] = []
        with open(files[0], encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    action = json.loads(line)
                    replay = action["replayChatItemAction"]
                    offset = float(replay.get("videoOffsetTimeMsec", 0)) / 1000.0
                    item = replay["actions"][0]["addChatItemAction"]["item"]
                    renderer = item.get("liveChatTextMessageRenderer")
                    if not renderer:
                        continue  # membership/superchat/system events skipped
                    author = renderer.get("authorExternalChannelId", "")
                    if author:
                        out.append((offset, author))
                except (KeyError, IndexError, ValueError, json.JSONDecodeError):
                    continue
        return out


# ---- curve ------------------------------------------------------------------


def _chatters_to_curve(times: list[tuple[float, str]], duration: float) -> np.ndarray | None:
    """UNIQUE chatters per BIN_SECONDS window -> per-second percentile curve.

    Unique authors (not message counts) so gifted-sub message storms and
    emote spam from a handful of accounts can't fabricate a hype moment.
    Percentile-ranked within the video, like the audio/visual signals.
    """
    if not times or duration <= BIN_SECONDS:
        return None
    covered = max(t for t, _ in times)
    if covered < duration * 0.5:
        # Chat replay only covers part of the VOD (partial fetch / muted
        # sections) — a half-blind signal would falsely zero the uncovered
        # half, so skip it entirely.
        print(f"      (chat covers only {covered / duration:.0%} of the video — skipping)")
        return None
    n_bins = int(duration // BIN_SECONDS) + 1
    bins: list[set] = [set() for _ in range(n_bins)]
    for t, who in times:
        b = int(t // BIN_SECONDS)
        if 0 <= b < n_bins:
            bins[b].add(who)
    density = np.array([len(b) for b in bins], dtype=np.float32)
    if density.max() <= 0:
        return None
    # Percentile-rank normalize (same convention as audio/visual signals).
    order = density.argsort().argsort().astype(np.float32)
    ranked = order / max(density.size - 1, 1)
    curve = np.repeat(ranked, BIN_SECONDS)[: int(duration) + 1]
    print(
        f"      Chat hype curve built: {len(times)} messages, "
        f"{int(density.max())} peak unique chatters/{BIN_SECONDS}s"
    )
    return curve
