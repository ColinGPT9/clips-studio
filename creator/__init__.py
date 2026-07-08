"""Creator Profiles + Creator Intelligence.

Sits ABOVE the video/source layer: a creator profile represents the person or
group, platform accounts represent their channels on YouTube/Twitch/Kick, and
the knowledge base holds structured facts extracted from their processed
videos (topics, catchphrases, collaborators, announced events).

Everything here is optional and failure-safe: a video with no resolved
creator, or a failed knowledge extraction, processes exactly as before.
Fully local — knowledge lives in the same SQLite state DB.
"""
