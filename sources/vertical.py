"""Downloading the vertical version of a video, for Vertical Live (core/modes.py).

A vertical live's recording can come several ways:
- A YouTube vertical-only live, or the separate "-vert" broadcast Streamlabs
  Dual Output makes: the replay is portrait-only.
- A dual-format stream: one video, possibly with both shapes. YouTube's own
  dual stream and Twitch Dual Format ("both a horizontal and a vertical
  version of your Clips and VODs"; Twitch keeps the vertical one for 7 days).

So a Vertical Live download asks yt-dlp for portrait formats only, and a video
that has none is refused with what to do instead. It is never quietly swapped
for the horizontal version. The standard download is not touched by any of this.
"""

# Kept apart from the standard download of the same video, so a horizontal
# copy already on disk is never taken for the vertical one (or the reverse).
SUFFIX = "__vertical"

# YouTube: portrait H.264 plus m4a, AV1 avoided, as the standard selector
# does. The size cap is on the SHORT side ("res" in yt-dlp's sort), so a
# 1080x1920 live stays 1080x1920. The standard selector's height<=1080 would
# pick 608x1080 for it.
YOUTUBE_FORMAT = (
    "bv*[aspect_ratio<1][vcodec^=avc1]+ba[ext=m4a]"
    "/bv*[aspect_ratio<1][vcodec!^=av01]+ba[ext=m4a]"
    "/b[aspect_ratio<1]"
)
FORMAT_SORT = ["res:1080"]

# Twitch and Kick serve H.264 HLS renditions; take the best portrait one.
HLS_FORMAT = "best[aspect_ratio<1][ext=mp4]/best[aspect_ratio<1]"


def no_vertical_version(platform: str) -> str:
    """What to say when a video has no vertical version to download."""
    if platform == "youtube":
        return ("YouTube has no vertical version of this video. If the vertical live was a "
                "separate stream (its title often ends in \"-vert\"), use that video's link, "
                "or download the vertical MP4 and add it as a file.")
    if platform == "twitch":
        return ("Twitch didn't offer a vertical version of this VOD. Twitch keeps the vertical "
                "version of a Dual Format stream for 7 days after it ends. If you have the "
                "vertical recording as an MP4, add it as a file.")
    return (f"{platform.title()} didn't offer a vertical version of this video. If you have the "
            "vertical recording as an MP4, add it as a file.")


def is_missing_format(error: str) -> bool:
    """yt-dlp's answer when no format matches the request."""
    return "Requested format is not available" in error
