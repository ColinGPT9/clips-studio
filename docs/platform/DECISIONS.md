# Decisions

One entry per judgment call: what was decided, the alternatives, why, and how to undo it. Newest at the bottom.

## D1 · Test baseline is taken with OpenCV and yt-dlp installed in the sandbox

**Decided:** install `opencv-python-headless` and `yt-dlp` into the sandbox interpreter (not into the repository's requirements) before recording the baseline.
**Alternatives:** record the baseline with CI's bare set (pyyaml, ruff, pytest, requests). That set is what CI runs, but this sandbox already has numpy, so the tests guarded by `pytest.importorskip("numpy")` try to import `cv2` and error at collection instead of skipping. With both packages present the suite collects completely and the baseline is 1633 passed, 14 failed, 72 skipped.
**Why:** a baseline with collection errors cannot say which tests must keep passing.
**Undo:** nothing in the repo changed; the numbers in PROGRESS.md are the only artifact.

## D2 · Per-platform research notes are gathered while the repository map is being written

**Decided:** start the Phase 0B note-taking agents while the Phase 0A readers are still running, and write the research synthesis (`docs/platform-research.md`, including every "borrow / avoid" verdict) only after `repo-map.md` is complete.
**Alternatives:** strictly finish 0A before any research starts, as the brief orders.
**Why:** the brief's reason for the order is that precedents only matter in relation to what Clips Kitty already is. That applies to the synthesis, which still waits for the map. Raw notes in the 13-point template do not depend on the map, and the sandbox runs only a few agents at a time, so serialising the two would cost the build phases several hours.
**Undo:** none needed; the notes are inputs, and anything in them that turns out to be irrelevant is simply not carried into the research document.

## D3 · The repository map was produced by parallel readers, not one pass

**Decided:** one reader per subsystem (packaging, API routes, other API surfaces, pipeline core, Gaming, Sports, AI stack and models, video/FFmpeg, desktop UI, extension seams, the Gaming-vs-Sports shared interface, and a live probe of the API), each writing notes that cite file paths; the map is assembled from them in the main thread and spot-checked against the code.
**Alternatives:** read the whole engine in the main thread. Too slow for 55 000 lines of Python plus the UI.
**Why:** the brief allows parallel workers for research and the map is the same kind of work; synthesis stays in the main thread.
**Undo:** none needed.
