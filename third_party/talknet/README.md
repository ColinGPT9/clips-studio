# TalkNet-ASD — vendored, do not edit

Copied verbatim from [TaoRuijie/TalkNet-ASD](https://github.com/TaoRuijie/TalkNet-ASD)
(MIT, © 2021 Tao Ruijie). The licence sits beside this file in `LICENSE.md`.

**Nothing in this folder should be modified.** An edited copy is no longer the
code that was audited and licensed, and it can no longer be re-fetched or
upgraded without first working out which differences were deliberate. If
something here needs to behave differently, change the adapter instead:

    video/asd.py

That file owns loading, the input format and the scoring call, and is the only
place the rest of the app touches this model.

## Why the linter and scanner skip it

Vendored source is excluded from `ruff` (`pyproject.toml`) and from CodeQL
(`.github/codeql/codeql-config.yml`). Both would otherwise report real style
problems in it — unused imports, an unclosed file, a `== None` — whose only
correct fix is to leave them alone. `.gitattributes` marks the folder vendored
so GitHub keeps it out of language stats and collapses it in diffs, and
`CODEOWNERS` requires a review for anything landing here.

Genuine security problems in a dependency still matter; they just arrive
through upstream advisories rather than style rules run over a snapshot.

## What was taken, and what was not

Only the seven files a forward pass needs:

    talkNet.py, loss.py
    model/talkNetModel.py, audioEncoder.py, visualEncoder.py, attentionLayer.py

Left behind: the S3FD face detector (YOLO already provides face tracks), the
training loop, the AVA dataset tooling and the demo script.

## Updating it

Re-fetch rather than patch:

    python scripts/fetch_asd_model.py   # weights, into models/ (gitignored)

The source files were fetched from `raw.githubusercontent.com` at the paths
listed above. If upstream changes, replace the files wholesale and re-check
`video/asd.py` against the new interface — that is the seam this arrangement
exists to protect.
