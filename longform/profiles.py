"""Longform rendering profiles.

The existing Shorts profile is implicit and untouched (no "profile" key in
render_opts). Every longform mode outputs 1920x1080 16:9 into its own
subfolder under the video's clip directory.
"""

PROFILES: dict[str, dict] = {
    "short_clips": {
        "label": "Short Clips",
        "subdir": "Longform/Short Clips",
        "min_duration": 10,
        "max_duration": 60,
        "ready": True,
    },
    "clips_140": {
        "label": "Clips (up to 140s)",
        "subdir": "Longform/Clips",
        "min_duration": 10,
        "max_duration": 140,
        "ready": True,
    },
    "highlights": {
        "label": "Highlights",
        "subdir": "Longform/Highlights",
        "ready": False,  # phase 3
    },
    "edited_stream": {
        "label": "Edited Stream",
        "subdir": "Longform/Edited Streams",
        "ready": False,  # phase 2
    },
}
