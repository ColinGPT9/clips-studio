# Resume here

Written 2026-08-14, updated 2026-08-15. Delete this file once the app is live.

## Ready to submit

```
release\ClipsStudio-1.1.2-x64.appx      7.60 GB
```

Unsigned, which is correct: Microsoft re-signs after certification.

**Verified end to end in the installed package**, not just built:

| Stage | Result |
|---|---|
| Install, launch, engine, bundled Ollama | all start |
| Model switching inside the package | 200, persists (was 500 before the config fix) |
| Transcription, audio and visual analysis | run |
| YOLO detection from the bundled weights | runs, no download attempt |
| Model scoring through the packaged Ollama | runs |
| Rendering | **8 clips, 8 files, 30.8 MB on disk** |
| Settings written outside the read-only package | `%LOCALAPPDATA%\Clips Studio\settings.yaml` |

Nothing about the pipeline is unverified any more.

## Everything the submission needs

| | Where |
|---|---|
| Every field, in Partner Center's order | `docs/msstore-submission-sheet.md` |
| Listing copy, features, search terms | `docs/store-listing.md` |
| Four screenshots | `docs/store-screenshots/` |
| Box art, poster, hero | `docs/store-art/` |
| Full walkthrough and reasoning | `docs/MSSTORE.md` |
| Progress tracker | `docs/msstore-checklist.md` |

## Do not forget at submission time

- Tick **live generative AI** (policy 11.16)
- Tick **third-party purchase API** for the PayPal donate link (policy 10.8.2)
- Set **manual publish**, so the certification report can be read first
- Next release uses **Start update** on the same product, never a new one

## Worth knowing

- The YOLO download bug **is in the shipped 0.1.2 installer too**. Anyone whose
  first video failed with a download error hit it. Recorded in CHANGELOG under
  Unreleased.
- The same end-to-end test has never been run against the **NSIS** build. Worth
  doing before 0.1.3: `python tests/assets/make_sample_video.py`, then import
  it and lower `clips.min_score` so candidates actually render.
- `build\msix-signing\` holds a 7.1 GB signed copy, only needed to reinstall
  locally. Safe to delete; `sign_msix.py` regenerates it.
- The Windows App Certification Kit cannot run here: `appcert.exe` ships with
  the Windows SDK, which is not installed.
