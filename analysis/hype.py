"""Audience hype signals, all optional and failure-safe.

Processing works identically without them: audience data only ever ADDS a
small scoring bonus and can also seed candidate windows around audience peaks.

Sources:
- Twitch VODs: the public web GQL endpoint every chat-replay tool uses.
  The Client-ID is scraped from twitch.tv itself at call time, because
  Twitch rotates it (hardcoded IDs from 2023-era tools now 400).
- YouTube, authenticated: the creator's organic audience-retention curve from
  YouTube Analytics, when a cached OAuth token includes yt-analytics.readonly.
  The raw retention curve is locally detrended before percentile ranking so
  ordinary retention decay does not make the start of every video look "hot".
- YouTube, public fallback: the "most replayed" heatmap yt-dlp exposes for
  popular videos, then chat replay (live_chat) for finished live streams.
- Kick: NOT possible — Kick deletes chat when a stream ends and has no replay
  endpoint (verified 2026-07); Kick hype comes from audio/visual signals only.

Gift-sub / bits resistance: the Twitch curve counts UNIQUE CHATTERS per time
bin, not messages. A gifted-sub bomb produces a burst of messages from few
humans (plus bots), which barely moves unique-chatter density — so fake "hype"
from sub trains doesn't outrank a genuinely popping chat. The bonus is also
hard-capped in fusion so transcript/visual context stays dominant.
"""

import json
import os
import re
import time
import urllib.request
from datetime import date
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
_YT_ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
_RETENTION_BASELINE_RADIUS = 4
_RETENTION_IGNORE_START_RATIO = 0.05

# Clips Kitty consumes audience as a CLIP-level signal: fusion averages the
# curve across each candidate and only bonuses windows whose mean is near the
# top. Organic retention, however, arrives as narrow 1%-of-video samples.
# Spread each local-retention peak into a short, gently-decaying interest zone
# so a real audience moment can survive that clip-window averaging without
# moving the peak or inventing new peaks.
_RETENTION_CLIP_RADIUS_SECONDS = 12
_RETENTION_EDGE_WEIGHT = 0.925


def audience_curve(url: str, video_id: str, duration: float) -> np.ndarray | None:
    """Per-second 0..1 hype curve for this video, or None when the platform
    has no fetchable audience data. Never raises."""
    try:
        if video_id.startswith("tw_"):
            times = _twitch_chat(video_id[3:], duration)
            return _chatters_to_curve(times, duration)
        if video_id.startswith("kick_"):
            return None  # Kick retains no chat after the stream ends

        # YouTube: private organic retention is the best signal when the user
        # has already authorized it. No token / wrong channel / no data is a
        # normal condition, so each case falls through to the public sources.
        organic = _youtube_organic_retention(video_id, duration)
        if organic is not None:
            return organic

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


def _youtube_token_path() -> Path:
    """The token written by `python main.py auth`.

    Electron passes CLIPS_STUDIO_DATA_DIR for installed builds. A source
    checkout with default settings resolves to repo/data. A custom CLI data
    directory that is not also exported through the environment simply means
    this optional signal is unavailable, so the public fallback still works.
    """
    override = os.environ.get("CLIPS_STUDIO_DATA_DIR")
    if override:
        return Path(override) / "youtube_token.json"

    from core.paths import resolve_data_dir

    return resolve_data_dir({}) / "youtube_token.json"


def _youtube_organic_retention(video_id: str, duration: float) -> np.ndarray | None:
    """Creator-only organic audience retention via YouTube Analytics.

    This function is deliberately non-interactive. It never opens a browser:
    if the cached token is absent, lacks the Analytics scope, belongs to an
    account that cannot read this video, or the video has no retention yet,
    return None and let audience_curve() use the existing public path.
    """
    token_path = _youtube_token_path()
    if not token_path.exists():
        return None

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials.from_authorized_user_file(str(token_path))

        if not creds.has_scopes([_YT_ANALYTICS_SCOPE]):
            return None

        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_path.write_text(creds.to_json(), encoding="utf-8")

        if not creds.valid:
            return None

        analytics = build(
            "youtubeAnalytics",
            "v2",
            credentials=creds,
            cache_discovery=False,
        )
        report = analytics.reports().query(
            ids="channel==MINE",
            startDate="2005-04-23",
            endDate=date.today().isoformat(),
            metrics="audienceWatchRatio",
            dimensions="elapsedVideoTimeRatio",
            filters=f"video=={video_id};audienceType==ORGANIC",
            sort="elapsedVideoTimeRatio",
        ).execute()

        rows = report.get("rows") or []
        curve = _retention_rows_to_curve(rows, duration)
        if curve is not None:
            print(f"      YouTube organic retention loaded ({len(rows)} bins, OAuth)")
        return curve
    except Exception:
        # Expected fallbacks include: token revoked, Analytics API disabled,
        # someone else's video, and a new/low-view video with no retention.
        return None


def _retention_rows_to_curve(rows: list, duration: float) -> np.ndarray | None:
    """Convert Analytics retention rows into Clips Kitty's per-second 0..1 signal.

    audienceWatchRatio naturally slopes down through almost every video. Using
    the raw values would therefore label the intro as the hottest part even
    when viewers never replayed or held there. Instead each 1%-of-video point
    is compared with the median on both sides (the same local-prominence idea
    validated against YouTube Studio), then those lifts are percentile-ranked
    within the video, matching the normalization convention used by the other
    Clips Kitty signals.

    The first 5% is deliberately suppressed: startup retention is dominated by
    autoplay, immediate exits and values over 100%, not a meaningful "moment".

    Finally, the per-second prominence curve is converted into a clip-scale
    interest envelope. Clips Kitty averages audience values over a whole clip;
    a genuine 1%-bin retention spike would otherwise be diluted almost to
    nothing inside a 25-60s candidate. The envelope keeps the maximum at the
    exact same second and lets its influence decay gently for 12s on either
    side. This is analogous to turning a point measurement into the short
    "hot zone" that the existing Most Replayed/chat curves already represent.
    """
    if duration <= 0 or len(rows) < 5:
        return None

    points: list[tuple[float, float]] = []
    for row in rows:
        try:
            ratio = float(row[0])
            watch = float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if np.isfinite(ratio) and np.isfinite(watch) and 0.0 <= ratio <= 1.0:
            points.append((ratio, watch))

    if len(points) < 5:
        return None

    points.sort(key=lambda p: p[0])

    # Duplicate ratios are not expected, but collapsing them avoids np.interp
    # ambiguity if the API ever returns one.
    collapsed: list[tuple[float, float]] = []
    for ratio, watch in points:
        if collapsed and abs(collapsed[-1][0] - ratio) < 1e-9:
            collapsed[-1] = (ratio, max(collapsed[-1][1], watch))
        else:
            collapsed.append((ratio, watch))

    ratios = np.asarray([p[0] for p in collapsed], dtype=np.float32)
    values = np.asarray([p[1] for p in collapsed], dtype=np.float32)
    n = values.size
    if n < 5:
        return None

    lift = np.empty(n, dtype=np.float32)

    for i, current in enumerate(values):
        left = values[max(0, i - _RETENTION_BASELINE_RADIUS):i]
        right = values[i + 1:min(n, i + _RETENTION_BASELINE_RADIUS + 1)]

        if left.size and right.size:
            # Using the higher side is conservative on a naturally declining
            # retention curve: only an actual local rise earns a strong lift.
            baseline = max(float(np.median(left)), float(np.median(right)))
        elif left.size:
            baseline = float(np.median(left))
        elif right.size:
            baseline = float(np.median(right))
        else:
            baseline = float(current)

        lift[i] = float(current) - baseline

    # The intro behaves unlike the rest of a retention graph; make it cold
    # before percentile ranking rather than letting 100%+ startup values win.
    intro = ratios < _RETENTION_IGNORE_START_RATIO
    if intro.any():
        floor = float(lift.min()) - max(float(np.ptp(lift)), 1e-6) - 1.0
        lift[intro] = floor

    # Same percentile-rank convention as analysis/fusion.py::_pct.
    order = lift.argsort(kind="stable").argsort(kind="stable").astype(np.float32)
    ranked = order / max(n - 1, 1)

    seconds = np.arange(max(int(duration) + 1, 2), dtype=np.float32)
    sample_seconds = np.clip(ratios * float(duration), 0.0, float(duration))

    if sample_seconds[0] > 0:
        sample_seconds = np.insert(sample_seconds, 0, 0.0)
        ranked = np.insert(ranked, 0, ranked[0])
    if sample_seconds[-1] < duration:
        sample_seconds = np.append(sample_seconds, float(duration))
        ranked = np.append(ranked, ranked[-1])

    point_curve = np.interp(seconds, sample_seconds, ranked).astype(np.float32)
    return _clip_scale_interest(point_curve)


def _clip_scale_interest(curve: np.ndarray) -> np.ndarray:
    """Turn point-like retention prominence into short clip-scale hot zones.

    For every second, nearby high-retention values can raise its interest, with
    a linear decay from 1.00 at the measured peak to 0.925 at +/-12 seconds.
    This is a MAX envelope, not smoothing/averaging: measured peaks never move
    and weaker neighbours cannot reduce them. Values stay in 0..1.

    Why 12 seconds: Clips Kitty grows signal candidates to ~25 seconds. A
    +/-12s envelope therefore makes one genuine retention peak meaningful over
    approximately one candidate-sized window, while remaining much narrower
    than the 60s maximum clip length.
    """
    if curve.size == 0:
        return curve

    radius = max(0, int(_RETENTION_CLIP_RADIUS_SECONDS))
    if radius == 0:
        return curve.astype(np.float32, copy=True)

    base = np.asarray(curve, dtype=np.float32)
    out = base.copy()

    for offset in range(1, radius + 1):
        frac = offset / radius
        weight = 1.0 - (1.0 - _RETENTION_EDGE_WEIGHT) * frac

        # A peak can influence the neighbouring second on either side, but
        # never above its own height and never by shifting its true maximum.
        out[offset:] = np.maximum(out[offset:], base[:-offset] * weight)
        out[:-offset] = np.maximum(out[:-offset], base[offset:] * weight)

    return np.clip(out, 0.0, 1.0).astype(np.float32)


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
