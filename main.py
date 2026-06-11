"""CLI entry point.

For YouTubers — three commands to full automation:
    python main.py channels add @YourHandle    # paste your handle or channel URL
    python main.py run                         # daemon: watch, clip, schedule
    python main.py status                      # see what it's done

More:
    python main.py process <url>               # one video, end-to-end
    python main.py channels list / remove <id>
    python main.py models                      # installed models + GPU guide
    python main.py models use gemma3:12b       # switch the LLM (one command)
"""

import argparse
import sys
from pathlib import Path

import yaml

from core.pipeline import process_video
from core.scheduler import run_daemon
from core.state import StateDB

CONFIG_PATH = Path(__file__).resolve().parent / "config" / "settings.yaml"


def load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # The "quick setup" block at the top of settings.yaml uses flat,
    # non-coder-friendly keys. Normalize them onto the full structure here
    # so the rest of the codebase only ever sees one shape.
    model = config.get("model")
    if model:
        spec = str(model) if "/" in str(model) else f"ollama/{model}"
        config.setdefault("llm", {})["backend"] = spec

    channel = config.get("channel")
    if channel and str(channel).strip():
        config.setdefault("channels", [])
        if channel not in config["channels"]:
            config["channels"].insert(0, str(channel).strip())

    if "auto_upload" in config:
        config.setdefault("upload", {})["enabled"] = bool(config["auto_upload"])

    privacy = str(config.get("privacy", "")).strip().lower()
    if privacy in ("public", "unlisted", "private"):
        config.setdefault("upload", {})["privacy"] = privacy

    return config


def main() -> int:
    # LLM titles may contain emoji; Windows consoles often use cp1252 and
    # would crash the whole pipeline on a mere print(). Degrade gracefully.
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Local AI YouTube Shorts pipeline")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    sub = parser.add_subparsers(dest="command", required=True)

    p_process = sub.add_parser("process", help="Process one video URL end-to-end")
    p_process.add_argument("url", help="YouTube video URL")
    p_process.add_argument("--force", action="store_true", help="Reprocess even if already done")

    sub.add_parser("run", help="Run the automation daemon (RSS monitor + scheduler)")
    sub.add_parser("status", help="Show processing/scheduling state")
    sub.add_parser("auth", help="One-time YouTube authorization (opens browser)")
    sub.add_parser("upload", help="Upload today's scheduled clips now")

    p_channels = sub.add_parser("channels", help="Manage monitored channels")
    ch_sub = p_channels.add_subparsers(dest="channels_command", required=True)
    p_ch_add = ch_sub.add_parser("add", help="Add a channel by @handle, URL, or ID")
    p_ch_add.add_argument("channel", help="e.g. @MrBeast, a channel URL, or UC... id")
    ch_sub.add_parser("list", help="List monitored channels")
    p_ch_rm = ch_sub.add_parser("remove", help="Stop monitoring a channel")
    p_ch_rm.add_argument("channel", help="Channel ID, @handle, or URL")

    p_models = sub.add_parser("models", help="Show installed LLMs and switch between them")
    p_models.add_argument("action", nargs="?", choices=["use"], help="'use' to switch models")
    p_models.add_argument("model", nargs="?", help="Ollama model tag, e.g. gemma3:12b")

    args = parser.parse_args()
    config = load_config(args.config)
    db = StateDB(Path(config["paths"]["data_dir"]) / "state.db")

    try:
        if args.command == "process":
            clips = process_video(args.url, config, db, force=args.force)
            if clips:
                print(f"\nDone. {len(clips)} clip(s) created:")
                for clip in clips:
                    print(f"  {clip.path}  (score {clip.candidate.score})")
            return 0

        if args.command == "run":
            run_daemon(config, db)
            return 0

        if args.command == "status":
            _print_status(db)
            return 0

        if args.command == "auth":
            from publish.youtube_shorts import YouTubeShortsPublisher

            publisher = YouTubeShortsPublisher(
                client_secret=Path(config["upload"]["client_secret"]),
                token_path=Path(config["paths"]["data_dir"]) / "youtube_token.json",
            )
            publisher.authenticate(interactive=True)
            print("YouTube authorized. Token saved — uploads can now run unattended.")
            print("Turn on auto-posting by setting  auto_upload: true  in config/settings.yaml.")
            return 0

        if args.command == "upload":
            from core.scheduler import upload_scheduled

            config.setdefault("upload", {})["enabled"] = True  # explicit command overrides the flag
            db.promote_queued_clips(config["upload"]["daily_limit"])
            n = upload_scheduled(config, db)
            print(f"{n} clip(s) uploaded." if n else "Nothing uploaded.")
            return 0

        if args.command == "channels":
            return _handle_channels(args, db)

        if args.command == "models":
            return _handle_models(args, config)
    finally:
        db.close()

    return 1


def _handle_channels(args, db: StateDB) -> int:
    from sources.youtube import resolve_channel

    if args.channels_command == "add":
        print(f"Resolving {args.channel!r}...")
        try:
            info = resolve_channel(args.channel)
        except Exception as e:
            print(f"Could not resolve channel: {e}")
            return 1
        db.add_channel(info["channel_id"], info["name"])
        print(f"Now monitoring: {info['name']} ({info['channel_id']})")
        print("Start the daemon with:  python main.py run")
        return 0

    if args.channels_command == "list":
        channels = db.list_channels()
        if not channels:
            print("No channels yet. Add yours with:  python main.py channels add @YourHandle")
            return 0
        for row in channels:
            print(f"  {row['channel_id']}  {row['name']}")
        return 0

    if args.channels_command == "remove":
        target = args.channel
        if not target.startswith("UC"):
            from sources.youtube import resolve_channel
            target = resolve_channel(target)["channel_id"]
        if db.remove_channel(target):
            print(f"Removed {target}.")
            return 0
        print(f"{target} was not in the channel list.")
        return 1

    return 1


def _handle_models(args, config: dict) -> int:
    from llm.manager import RECOMMENDATIONS, installed_models, switch_model

    host = config["llm"].get("ollama_host", "http://localhost:11434")
    current = config["llm"]["backend"]

    if args.action == "use":
        if not args.model:
            print("Usage: python main.py models use <model-tag>   e.g. gemma3:12b")
            return 1
        try:
            installed = {m["name"] for m in installed_models(host)}
        except Exception:
            installed = set()
        if installed and args.model not in installed:
            print(f"'{args.model}' is not pulled in Ollama yet. Run:  ollama pull {args.model}")
            print("Then re-run this command.")
            return 1
        spec = switch_model(args.config if hasattr(args, "config") else CONFIG_PATH, args.model)
        print(f"Switched LLM backend to {spec}. Everything else stays the same.")
        return 0

    try:
        models = installed_models(host)
    except Exception as e:
        print(f"Could not reach Ollama at {host} ({e}). Is it running?")
        return 1

    print(f"Active model: {current}\n")
    print("Installed in Ollama:")
    for m in models:
        marker = " <- active" if f"ollama/{m['name']}" == current else ""
        print(f"  {m['name']:24} {m['size_gb']:5.1f} GB{marker}")
    print("\nUpgrade guide (better GPU = bigger Gemma = better clip selection):")
    for hw, model, note in RECOMMENDATIONS:
        print(f"  {hw:18} {model:28} {note}")
    print("\nSwitch with:  ollama pull <model>   then   python main.py models use <model>")
    return 0


def _print_status(db: StateDB) -> None:
    s = db.summary()
    print("Videos:")
    for row in s["videos"]:
        print(f"  {row['status']:12} {row['n']}")
    print("Clips:")
    for row in s["clips"]:
        print(f"  {row['status']:12} {row['n']}")
    print("Duplicates/rejections:")
    for row in s["rejections"]:
        print(f"  {row['reason']:22} {row['n']}")
    print(f"Scheduled today: {s['scheduled_today']}")


if __name__ == "__main__":
    sys.exit(main())
