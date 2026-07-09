"""Longform: horizontal 1920x1080 outputs alongside the Shorts pipeline.

A separate system from the Shorts feature — the existing vertical workflow
is never modified — but built almost entirely out of the same parts: the
same download/cache, transcription, multimodal scoring, ranking, duplicate
prevention, metadata generation and clip rendering, driven with different
duration bounds and a landscape rendering profile. Outputs are regular clip
rows, so Clip Studio and the full editor work on them unchanged.
"""
