"""Discord links: a channel or message link pasted into a campaign, and a
webhook URL pasted as a destination.

A webhook URL carries the token that lets anyone post to its channel. Nothing
here puts it into a result, a problem message or an exception: a parsed link
names its kind and ids, and the token comes back only from parse_webhook_url,
to be stored with core/secrets.
"""

import re
from dataclasses import asdict, dataclass

#: A Discord id. Kept as text everywhere: it overflows JavaScript numbers.
SNOWFLAKE = r"\d{17,20}"
SNOWFLAKE_RE = re.compile(rf"^{SNOWFLAKE}$")

_HOST = r"(?:(?:ptb|canary|www)\.)?discord(?:app)?\.com"
_CHANNELS = re.compile(
    rf"^{_HOST}/channels/(?P<guild>{SNOWFLAKE}|@me)"
    rf"(?:/(?P<channel>{SNOWFLAKE})(?:/(?P<message>{SNOWFLAKE}))?)?/?$",
    re.IGNORECASE,
)
_WEBHOOK = re.compile(
    rf"^{_HOST}/api(?:/v\d+)?/webhooks/(?P<id>{SNOWFLAKE})/(?P<token>[\w-]+)/?$", re.IGNORECASE
)
_INVITE = re.compile(rf"^(?:(?:www\.)?discord\.gg|{_HOST}/invite)/[\w-]+/?$", re.IGNORECASE)

COPY_LINK = "right-click the channel → Copy Link"
INVITE_PROBLEM = f"That's an invite link. Paste the channel's link: {COPY_LINK}."
WEBHOOK_PROBLEM = "That's a webhook link: add it in Settings → Discord → Destinations"
DIRECT_PROBLEM = f"That's a direct message, not a server channel. Paste the channel's link: {COPY_LINK}."
NOT_DISCORD_PROBLEM = f"That isn't a Discord channel link. In Discord, {COPY_LINK}, then paste it here."
BAD_WEBHOOK = (
    "That isn't a Discord webhook link. In Discord: Server Settings → Integrations → Webhooks "
    "→ Copy Webhook URL."
)


@dataclass
class DiscordLink:
    kind: str  # channel | message | guild | webhook | invite | none
    guild_id: str = ""
    channel_id: str = ""
    message_id: str = ""
    problem: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _bare(text) -> str:
    """The link without its scheme, query or fragment, ready to match. Discord
    wraps a link in <> to hide its preview, and people paste that too."""
    link = str(text or "").strip().strip("<>").strip()
    link = re.sub(r"^[a-z]+://", "", link, flags=re.IGNORECASE)
    return re.split(r"[?#]", link, maxsplit=1)[0]


def parse_link(text) -> DiscordLink:
    """What a pasted Discord link points at. Never echoes a webhook's token."""
    link = _bare(text)
    if not link:
        return DiscordLink("none")
    if _WEBHOOK.match(link):
        return DiscordLink("webhook", problem=WEBHOOK_PROBLEM)
    if _INVITE.match(link):
        return DiscordLink("invite", problem=INVITE_PROBLEM)
    found = _CHANNELS.match(link)
    if found is None:
        return DiscordLink("none", problem=NOT_DISCORD_PROBLEM)
    guild, channel, message = found.group("guild"), found.group("channel"), found.group("message")
    if guild == "@me":
        return DiscordLink("none", problem=DIRECT_PROBLEM)
    if message:
        return DiscordLink("message", guild, channel, message)
    if channel:
        return DiscordLink("channel", guild, channel)
    return DiscordLink("guild", guild)


def parse_channel_link(text) -> DiscordLink:
    """A campaign's channel link: a channel, a message in it (its channel is
    the one meant) or a server. Raises ValueError with the message to show for
    anything else: an invite, a webhook, a direct message, not Discord at all.
    An empty link is no link (kind "none"), not an error."""
    link = parse_link(text)
    if link.kind in ("channel", "message", "guild") or (link.kind == "none" and not link.problem):
        return link
    raise ValueError(link.problem)


def parse_webhook_url(text) -> tuple[str, str]:
    """(webhook_id, token) from a pasted webhook URL. Raises ValueError with a
    message that never contains what was pasted."""
    found = _WEBHOOK.match(_bare(text))
    if found is None:
        raise ValueError(BAD_WEBHOOK)
    return found.group("id"), found.group("token")


def channel_url(guild_id: str, channel_id: str = "", message_id: str = "") -> str:
    """The link that opens a server, a channel or a message in Discord; '' when
    the server isn't known."""
    parts = [p for p in (guild_id, channel_id, message_id) if p]
    if not guild_id or not all(SNOWFLAKE_RE.match(p) for p in parts):
        return ""
    if message_id and not channel_id:
        return ""
    return "https://discord.com/channels/" + "/".join(parts)
