"""What a plugin's manifest asks for, in the words the install screen uses.

The labels and the enforced-or-declared split are the ones in
docs/developers/permissions.md. "Enforced" means Clips Kitty decides what it
hands over (plugins/runner.py); "declared" means the developer states it and
nothing stops the plugin doing otherwise, because a plugin runs with the
user's own rights. Nothing here claims more than that.
"""

from __future__ import annotations

import sys

NOTICE = "This plugin is code from the internet. It runs on this PC with your rights."
ENFORCED = "enforced for the hand-over"
DECLARED = "declared by the developer"

LABELS = {
    "video.read": "Reads the video you process",
    "transcript.read": "Reads its transcript",
    "ffmpeg": "Uses Clips Kitty's FFmpeg",
    "ollama": "Uses your local AI model",
    "gpu": "Uses your graphics card",
    "network": "Connects to: {hosts}",
    "filesystem.read": "Reads files outside its own folder",
    "filesystem.write": "Writes files outside its own folder",
    "project.read": "Reads your clip library",
    "project.write": "Changes your clip library",
}
HANDED_OVER = ("video.read", "transcript.read", "ffmpeg", "ollama")

DATA = {
    "video": "your video",
    "video_link": "your video's link",
    "audio": "your video's audio",
    "frames": "frames from your video",
    "transcript": "your video's transcript",
}

EXECUTION = {
    "local": "Runs on this PC. The developer declares that nothing leaves your computer.",
    "remote": "Runs on a service on the internet: your data leaves this PC (see the warnings).",
    "hybrid": "Runs on this PC and uses a service on the internet (see the warnings).",
}

TIERS = {
    "official": "Official",
    "listed": "Listed · not reviewed by a person",
    "link": "Not listed · Clips Kitty has not checked this",
}

GPU = {"optional": "A graphics card helps but isn't needed", "recommended": "A graphics card is recommended",
       "required": "Needs a graphics card"}


def secrets_notice() -> str:
    account = "your Windows account" if sys.platform == "win32" else "your user account"
    return f"Your keys for this plugin are stored for {account}. Other plugins and programs running as you can read them."


def permission_lines(manifest: dict) -> list[dict]:
    """Each permission with its label and whether it is enforced."""
    hosts = ", ".join(str(h) for h in manifest.get("network") or []) or "nothing named"
    out = []
    for perm in manifest.get("permissions") or []:
        label = LABELS.get(perm, perm).format(hosts=hosts)
        out.append({"id": perm, "label": label, "enforcement": ENFORCED if perm in HANDED_OVER else DECLARED})
    return out


def data_warnings(manifest: dict) -> list[str]:
    """One ⚠ line per kind of data the plugin says leaves the PC."""
    out = []
    for entry in manifest.get("sends") or []:
        if isinstance(entry, str):
            out.append(f"⚠ Sends {DATA.get(entry, entry)} off this computer")
        elif isinstance(entry, dict):
            line = f"⚠ Sends {DATA.get(entry.get('data'), entry.get('data'))} to {entry.get('to', 'a service')}"
            if entry.get("when"):
                line += f", when {entry['when']}"
            out.append(line)
    return out


def requirement_lines(manifest: dict) -> list[str]:
    req = manifest.get("requirements") or {}
    out = []
    if req.get("gpu") in GPU:
        line = GPU[req["gpu"]]
        if req.get("vram_gb"):
            line += f", {req['vram_gb']:g} GB of video memory"
        out.append(line)
    if req.get("ram_gb"):
        out.append(f"{req['ram_gb']:g} GB of memory")
    if req.get("disk_gb"):
        out.append(f"{req['disk_gb']:g} GB of disk")
    if req.get("os"):
        names = {"windows": "Windows", "macos": "macOS", "linux": "Linux"}
        out.append("Works on " + ", ".join(names.get(o, o) for o in req["os"]))
    if req.get("software"):
        out.append("Needs " + ", ".join(map(str, req["software"])))
    return out


def model_lines(manifest: dict) -> list[dict]:
    """The models it lists, as the manifest states them (Phase 9 adds what
    is already installed and the sizes Clips Kitty can check)."""
    out = []
    for m in manifest.get("models") or []:
        if isinstance(m, dict):
            out.append({k: m.get(k) for k in ("name", "source", "id", "revision", "files", "license",
                                               "size_bytes", "gated") if m.get(k) is not None})
    return out


def describe(manifest: dict, *, tier: str = "link") -> dict:
    """Everything the install screen shows about what a plugin will do."""
    run = manifest.get("run") if isinstance(manifest.get("run"), dict) else {}
    secret_settings = [n for n, s in (manifest.get("settings") or {}).items()
                       if isinstance(s, dict) and s.get("type") == "secret"]
    service = manifest.get("service") if isinstance(manifest.get("service"), dict) else None
    return {
        "notice": None if tier == "official" else NOTICE,
        "tier": tier,
        "tier_text": TIERS.get(tier, tier),
        "execution": manifest.get("execution"),
        "execution_text": EXECUTION.get(manifest.get("execution"), ""),
        "permissions": permission_lines(manifest),
        "network": list(manifest.get("network") or []),
        "data_warnings": data_warnings(manifest),
        "requirements": requirement_lines(manifest),
        "models": model_lines(manifest),
        "service": ({"name": service.get("name"), "url": service.get("url"), "pricing": service.get("pricing"),
                     "required": bool(service.get("required"))} if service else None),
        "secrets": secret_settings,
        "secrets_notice": secrets_notice() if secret_settings else None,
        "python_packages": ("This plugin lists Python packages of its own. Installing them is planned; until then "
                            "it runs with a Python you already have, without them, and may not work."
                            if run.get("python_requirements") else None),
    }


def changes(old: dict, new: dict) -> dict:
    """What an update changes in what the plugin asks for: shown before it installs."""
    def sends(m):
        return set(data_warnings(m))

    old_perms, new_perms = set(old.get("permissions") or []), set(new.get("permissions") or [])
    old_hosts, new_hosts = set(old.get("network") or []), set(new.get("network") or [])
    return {
        "added_permissions": sorted(new_perms - old_perms),
        "removed_permissions": sorted(old_perms - new_perms),
        "added_hosts": sorted(new_hosts - old_hosts),
        "removed_hosts": sorted(old_hosts - new_hosts),
        "added_data_warnings": sorted(sends(new) - sends(old)),
        "execution_changed": old.get("execution") != new.get("execution"),
    }


def render_text(plan: dict) -> str:
    """A plan as plain text: the install screen without the buttons."""
    p, about = plan["plugin"], plan["details"]
    lines = [f"Install {p.get('name')} {p.get('version')}?",
             " · ".join(x for x in (p.get("id"), plan.get("source_text"), p.get("license")) if x),
             about["tier_text"], ""]
    if about.get("notice"):
        lines.append(about["notice"])
    if about.get("execution_text"):
        lines.append(about["execution_text"])
    lines += ["", "It will"]
    for perm in about["permissions"]:
        lines.append(f"  {perm['label']} ({perm['enforcement']})")
    lines += about["data_warnings"]
    if about["requirements"]:
        lines += ["Requirements"] + [f"  {line}" for line in about["requirements"]]
    for m in about["models"]:
        lines.append(f"Model  {m.get('name')}: {m.get('source')} {m.get('id')}"
                     + (f" · {m['license']}" if m.get("license") else ""))
    if about.get("service"):
        s = about["service"]
        lines.append(f"Service  {s['name']} · {'required' if s['required'] else 'optional'}"
                     + (f" · {s['pricing']}" if s.get("pricing") else ""))
    for key in ("secrets_notice", "python_packages"):
        if about.get(key):
            lines.append(about[key])
    update = plan.get("update")
    if update:
        lines += ["", f"Replaces {update['from']} ({update['direction']})"]
        for k, title in (("added_permissions", "New permissions"), ("added_hosts", "New hosts"),
                         ("added_data_warnings", "New data warnings")):
            if update[k]:
                lines.append(f"  {title}: {', '.join(update[k])}")
    for problem in plan.get("errors") or []:
        lines.append(f"✗ {problem}")
    for warning in plan.get("warnings") or []:
        lines.append(f"! {warning}")
    return "\n".join(lines)
