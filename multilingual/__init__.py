"""Multilingual publishing — a separate pipeline that runs AFTER clipping.

It consumes finished artifacts (a rendered clip and its caption lines) and
writes NEW files beside them: translated subtitle files, and optionally a
copy of the clip with translated captions burned in. It never participates
in clip selection, scoring, tracking, cropping, or the original export, so
nothing here can change how an existing clip turns out.

Nothing in core/, video/, or analysis/ imports this package. The only
touchpoints are additive: one job type in the worker and its own API
routes. It uses the same transcript and the same AI model the app already
uses: locally by default, where it costs nothing and needs no account or
network service, or the cloud model the user chose on their own key.

Stage 1 (implemented): translated subtitles.
Later stages (dubbing, voice preservation) plug in after translation
without changing anything here.
"""
