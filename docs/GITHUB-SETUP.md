# GitHub setup

Everything that can live in a file already does — CI, CodeQL, Dependabot,
labels, issue and PR templates. The rest are switches only a repository admin
can flip, and they are listed here so nobody has to guess what was meant to be
on.

Order matters a little: turn on **Actions permissions** and **CodeQL** before
**branch protection**, or protection will require checks that have never run
and every pull request will sit blocked.

---

## 1. Security (Settings → Code security)

| Setting | Why |
|---|---|
| **Dependabot alerts** | Tells you when something you depend on has a known vulnerability. |
| **Dependabot security updates** | Opens the fix as a PR automatically. |
| **Secret scanning** | Catches an API key or token committed by accident. Free on public repos. |
| **Push protection** | Blocks the push *before* the secret lands. This is the one that saves you. |
| **Private vulnerability reporting** | The route `SECURITY.md` sends people down. Without it, that link 404s. |

`.github/workflows/codeql.yml` handles code scanning itself — no setup beyond
letting Actions run.

## 2. Discussions (Settings → General → Features)

Tick **Discussions**. It gives people somewhere to ask "will this work on my
GPU" without opening a bug, which keeps Issues about actual defects.

Suggested categories: *Q&A*, *Show and tell* (clips people made), *Ideas*,
*Hardware and performance*.

## 3. Labels

```
Actions → Sync labels → Run workflow
```

Creates the set in `.github/labels.json`. Re-run it after editing that file.
It never deletes a label, since removing one strips it from every issue that
used it.

## 4. Branch protection (Settings → Rules → Rulesets)

Do this **after** CI has run at least once on a pull request, so the checks
exist to be selected.

Target `main`, and require:

- A pull request before merging
- Status checks: **Python**, **Desktop app**, **Website**
- Branches up to date before merging
- Conversation resolution before merging
- Block force pushes

**Leave "Require approvals" at 0 while you are the only maintainer** — set to
1 and you cannot merge your own work, which means either the rule gets
disabled in frustration or every change waits on a reviewer who does not
exist. Raise it the day someone else has commit rights.

Tick **"Do not allow bypassing the above settings"** only once you are
comfortable; as sole maintainer, keeping the bypass is a reasonable escape
hatch for a broken release.

## 5. Actions permissions (Settings → Actions → General)

- **Allow all actions and reusable workflows** — the workflows here use
  `actions/*` and `github/codeql-action/*` only.
- Workflow permissions: **Read repository contents** (each workflow requests
  the extra scopes it needs, and nothing more).

---

## What the CI actually proves

Worth being honest about, because a green tick invites more trust than it has
earned. A GitHub runner has no GPU, no Ollama and no real footage, so CI
cannot tell you a clip came out well. It checks:

- every Python module compiles
- `settings.yaml`, the workflows and the prompt files still parse and are not empty
- no credentials file has been committed
- the desktop app typechecks **and builds**
- every website link resolves, and no path is absolute (which would break on
  GitHub Pages, since a project site is served from a subdirectory)

**There is no test suite yet.** Clip quality, tracking and rendering are still
verified by watching real videos. That is the honest gap, and it is what §7 of
the roadmap is for.
