"""Daemon loop: RSS monitoring + processing queue + daily upload schedule.

Cycle (every poll_interval_minutes):
  1. Poll each configured channel's RSS feed; insert unseen videos as 'queued'.
  2. Process queued/interrupted videos through the pipeline (oldest first).
  3. Promote rendered clips into today's schedule, highest score first,
     never exceeding upload.daily_limit per calendar day. Clips over the
     limit stay 'queued' in SQLite — they survive restarts and drain on
     following days.

The 'scheduled' status means "selected for upload today". The future
YouTube upload module consumes scheduled clips and marks them 'uploaded';
until it exists, scheduled clips simply wait.
"""

import json
import time
import traceback
from pathlib import Path

from core.pipeline import process_video
from core.state import StateDB
from sources import youtube


def run_daemon(config: dict, db: StateDB) -> None:
    poll_seconds = config["poll_interval_minutes"] * 60
    channels = _configured_channels(config, db)
    if not channels:
        print("No channels to monitor yet. Add yours with:")
        print("  python main.py channels add @YourHandle")
        return

    names = ", ".join(c.get("name") or c["id"] for c in channels)
    print(f"Daemon started: monitoring {names} — polling every {config['poll_interval_minutes']} min. Ctrl+C to stop.")
    while True:
        try:
            _poll_channels(channels, db)
            _process_queue(config, db)
            _schedule_clips(config, db)
            upload_scheduled(config, db)
        except KeyboardInterrupt:
            print("\nStopping daemon.")
            return
        except Exception:
            # The daemon must outlive any single failure (network blips,
            # one broken video, Ollama restarting...).
            traceback.print_exc()

        try:
            time.sleep(poll_seconds)
        except KeyboardInterrupt:
            print("\nStopping daemon.")
            return


def _configured_channels(config: dict, db: StateDB) -> list[dict]:
    """Channels from the DB (added via `channels add`) merged with any listed
    in settings.yaml. YAML entries may be @handles or URLs — resolved here."""
    channels = [{"id": r["channel_id"], "name": r["name"]} for r in db.list_channels()]
    seen = {c["id"] for c in channels}

    for entry in config.get("channels") or []:
        raw = entry["id"] if isinstance(entry, dict) else entry
        try:
            info = youtube.resolve_channel(str(raw))
        except Exception as e:
            print(f"Skipping channel {raw!r} from settings.yaml: {e}")
            continue
        if info["channel_id"] not in seen:
            channels.append({"id": info["channel_id"], "name": info["name"]})
            seen.add(info["channel_id"])
    return channels


def _poll_channels(channels: list[dict], db: StateDB) -> None:
    known = db.known_video_ids()
    for channel in channels:
        channel_id = channel["id"]
        try:
            entries = youtube.poll_channel(channel_id)
        except Exception as e:
            print(f"RSS poll failed for {channel_id}: {e}")
            continue
        new = [e for e in entries if e["video_id"] not in known]
        for entry in new:
            db.upsert_video(entry["video_id"], channel_id=channel_id, title=entry["title"])
            print(f"New upload detected: {entry['title']} ({entry['video_id']})")


def _process_queue(config: dict, db: StateDB) -> None:
    # Includes interrupted statuses so a crash mid-video resumes here.
    pending = db.videos_with_status("queued", "downloaded", "transcribed", "analyzed")
    for row in pending:
        url = youtube.watch_url(row["video_id"])
        try:
            process_video(url, config, db)
        except Exception:
            print(f"Processing failed for {row['video_id']}:")
            traceback.print_exc()
            db.set_video_status(row["video_id"], "failed")


def _schedule_clips(config: dict, db: StateDB) -> None:
    daily_limit = config["upload"]["daily_limit"]
    promoted = db.promote_queued_clips(daily_limit)
    for row in promoted:
        print(f"Scheduled for upload today: {row['path']} (score {row['score']})")
    used = db.count_scheduled_on()
    if used >= daily_limit:
        print(f"Daily schedule full ({used}/{daily_limit}); remaining clips stay queued for tomorrow.")


def upload_scheduled(config: dict, db: StateDB) -> int:
    """Upload today's scheduled clips to YouTube. Returns how many succeeded.
    No-op unless auto_upload is enabled and `python main.py auth` has been run.
    Also called directly by the `upload` CLI command."""
    if not config["upload"].get("enabled"):
        return 0
    rows = db.clips_with_status("scheduled")
    if not rows:
        return 0

    from publish.youtube_shorts import YouTubeShortsPublisher  # lazy: google libs

    publisher = YouTubeShortsPublisher(
        client_secret=Path(config["upload"]["client_secret"]),
        token_path=Path(config["paths"]["data_dir"]) / "youtube_token.json",
        privacy=config["upload"].get("privacy", "public"),
    )
    try:
        publisher.authenticate(interactive=False)
    except Exception as e:
        print(f"Uploads paused: {e}")
        return 0

    uploaded = 0
    for row in rows:
        path = Path(row["path"])
        if not path.exists():
            print(f"Clip file missing, skipping: {path}")
            db.set_clip(row["id"], status="failed")
            continue
        hashtags = json.loads(row["hashtags"]) if row["hashtags"] else []
        description = (row["description"] or "").strip()
        description += "\n\n" + " ".join(hashtags + ["#Shorts"])
        try:
            youtube_id = publisher.upload(
                path,
                title=row["title"] or row["hook"] or path.stem,
                description=description,
                tags=hashtags,
            )
        except Exception as e:
            # Quota/auth errors affect every remaining upload — stop the batch,
            # leave clips 'scheduled' so the next cycle retries.
            print(f"Upload failed for {path.name}: {e}")
            break
        db.record_upload(row["id"], youtube_id)
        uploaded += 1
        print(f"Uploaded: https://youtube.com/shorts/{youtube_id}  ({path.name})")
    return uploaded
