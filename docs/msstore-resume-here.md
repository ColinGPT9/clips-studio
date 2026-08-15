# Resume here

Written 2026-08-14. Delete this file once the app is on the Store.

## State

The package is **built with every fix** and sits at:

```
release\ClipsStudio-1.1.2-x64.appx      7.60 GB
```

That is the file to upload. It is unsigned, which is correct: Microsoft
re-signs after certification.

Everything the submission needs is ready:

| | Where |
|---|---|
| Every form field, in Partner Center's order | `docs/msstore-submission-sheet.md` |
| Listing copy, features, search terms | `docs/store-listing.md` |
| Four screenshots | `docs/store-screenshots/` |
| Box art, poster, hero | `docs/store-art/` |
| Full walkthrough and the why behind each step | `docs/MSSTORE.md` |
| Progress tracker | `docs/msstore-checklist.md` |

## The one thing left undone

**This exact build has not been run end to end.** The previous build was, and
that is how the YOLO download bug was found: a real video through the installed
package died with

```
Download failure for .../yolov8n-pose.pt. Retry limit reached.
```

That is fixed and committed, and the package now contains the fix. What has
not happened is confirming a video processes to completion *on this build*.

**Signing is already done**, so skip that step. The signed copy is at
`build\msix-signing\ClipsStudio-1.1.2-x64.appx`, signature Valid, and the
original in `release\` is correctly still unsigned for upload.

Install it from an **admin** PowerShell:

```powershell
Import-Certificate -FilePath "build\msix-signing\test.cer" `
    -CertStoreLocation Cert:\LocalMachine\TrustedPeople
Add-AppxPackage "build\msix-signing\ClipsStudio-1.1.2-x64.appx"
```

(The certificate is probably still trusted from last time, in which case only
the second line is needed.) Then launch the app and process
`tests/assets/sample_video.mp4`. Generate that file first if it is gone:

```
python tests/assets/make_sample_video.py
```

It is synthetic, made by FFmpeg from nothing, so no third-party footage and
nobody's face is involved. Its audio peaks are at known times, so the clips it
produces say whether detection actually worked rather than only whether the
job finished.

Submitting without this is a judgement call, not a blocker. The bug it would
catch is the class of thing that only appears when the pipeline does real work,
and one of those has already been found and fixed.

## Also worth knowing

- The YOLO bug **is in the shipped 0.1.2 installer too**, not just the Store
  build. Anyone whose first video failed with a download error hit it. It is in
  the changelog under Unreleased.
- `build\msix-signing\` holds a 7.1 GB signed copy, only needed to reinstall
  locally. Safe to delete; `sign_msix.py` regenerates it.
- The Windows App Certification Kit cannot run on this machine: `appcert.exe`
  ships with the Windows SDK, which is not installed. Optional, but it catches
  manifest problems before Microsoft's testers do.
- No video has ever been taken end to end through the **NSIS** build either.
  The same test would be worth running there before 0.1.3.

## Do not forget at submission time

- Tick **live generative AI** (policy 11.16)
- Tick **third-party purchase API** for the PayPal donate link (policy 10.8.2)
- Set **manual publish**, so the certification report can be read before it
  goes live
- Next release uses **Start update** on the same product, never a new one
