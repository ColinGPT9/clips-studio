"""The new editor's media (editor suite step 6): finding the file a media id
names (resolver.py), serving it in byte ranges (fileio.py), what it is and
whether Chromium can play it (probe.py), and the editor cache under
data/media_cache/ (cache.py) with its proxies (proxy.py), waveform peaks
(peaks.py) and thumbnail sheets (thumbs.py); importing a person's own file
for editing (importing.py). The routes are server/media_api.py.

Engine only, and nothing here runs unless a media route is called.
core/pipeline.py never imports this package (tests/test_editor_import_guard.py).
Standard library only; peaks.py uses numpy when it is there.
"""
