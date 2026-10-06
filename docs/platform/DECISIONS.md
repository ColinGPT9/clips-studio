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

## D4 · Research stopped at the brief's rule

**Decided:** stop Phase 0B once every matrix cell was filled or marked "not confirmed" and every Appendix B question could be answered: 24 extension ecosystems and 16 gaming and video products. Framedrop's site could not be reached, so its row is marked "not confirmed" rather than researched further.
**Alternatives:** keep reading tier-3 products and incidents.
**Why:** brief §6.1: "Do not let it grow past that. The build phases need the rest of the night."
**Undo:** add notes under `docs/platform/research-notes/` and rows to the matrices.

## D5 · Five design defaults chosen in the research document, each listed for the owner in §7.7

**Decided:** (1) the manifest file is `clipskitty.yaml` at the plugin repository's root; (2) no "Verified" tier: tiers are Official, Listed (automated checks only), Installed from a link, Blocked; (3) the existing local API stays unauthenticated, and only the new plugin-manager routes (install, update, enable, remove) require the desktop app's per-session secret; (4) the registry lives in a `registry/` folder in this repository until a separate repository exists; (5) the SDK keeps the repository's AGPL-3.0 licence until the owner decides.
**Alternatives:** (1) `plugin.yaml` (too generic to grep for) or a `[tool.clipskitty]` table in `pyproject.toml` (ties the manifest to Python); (2) a "Verified" badge with no process behind it, which the brief forbids; (3) a token on every route, which breaks the OBS plugin, the MCP server and scripts; (4) a new repository, which the brief forbids tonight; (5) relicensing, which is the owner's legal call.
**Why:** each default is reversible and keeps existing clients working; the reasoning is in `docs/platform-research.md` §7.6 and §7.7.
**Undo:** each is a single choice in the architecture document and the code that follows it.

## D6 · httpx added to the sandbox and a second baseline recorded

**Decided:** install `httpx` 0.28.1 into the sandbox interpreter (not into the repository's requirements) and record a second baseline: 1879 passed, 14 failed, 23 skipped, with the same 14 failures as the first.
**Alternatives:** keep the first baseline, in which every `TestClient` test skips; Phase 2's contract tests would then skip here too and prove nothing.
**Why:** the brief asks for contract tests that pin current behaviour, which needs the API tests to run.
**Undo:** nothing in the repository changed; CI still installs only pyyaml, ruff, pytest and requests, so the new API tests keep the repository's `pytest.importorskip` guards and skip there.

## D7 · Architecture choices that shape the build (docs/platform-architecture.md)

**Decided:** (1) plugins run only as a child process with a job folder; nothing third-party is imported into the engine; (2) a plugin replaces only the detection step (`clip_direction` and `find_clips` in `process_video`), and Clips Kitty keeps titles, rendering, captions and the library; (3) a job names its plugin in one optional field, `pipeline: {id, version, settings}`, exclusive with Sports, Gaming scoring and Longform; (4) plugin progress is reported as the existing `analyze` stage with a `fraction`, so the UI's stage list is untouched; (5) a plugin's own Python packages are wheels only, hash-pinned, in its own environment, and a separate line the user agrees to at install; (6) a blocked plugin is refused at run time and flagged, never deleted by Clips Kitty; (7) the registry client's default index is the copy bundled with the app until the owner publishes one, so no URL is invented; (8) one previous version of each plugin is kept for rollback.
**Alternatives:** (1) in-process plugins as in ComfyUI or Blender, which give plugin code the engine's full access and cannot load in the frozen build; (2) plugins that own the whole pipeline, which would make every plugin re-implement rendering and captions; (3) separate `pipeline` and `pipeline_settings` fields; (4) a new `plugin` stage, which needs the UI's mirrored stage list changed; (5) letting pip build source packages at install, which runs their code before the user has used the plugin; (6) automatic removal, as VS Code's block list does; (7) a guessed GitHub URL; (8) keeping every version, which Colin's disk cannot afford.
**Why:** each keeps the existing API, engine and runtime as they are and follows a research finding (`docs/platform-research.md` §7.4, §7.6).
**Undo:** each is one section of the architecture document and, once built, one module.
