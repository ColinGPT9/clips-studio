# Mirrors

Clips Studio lives on GitHub, but people look for software in more than one
place. These mirrors exist so the project can be found by someone who never
visits GitHub — and so it survives any single platform.

Everything below is automated by
[`.github/workflows/mirror.yml`](../.github/workflows/mirror.yml), which runs on
every push to `main`. **Each mirror is skipped unless its secrets exist**, so the
workflow is harmless until you set one up, and starts working the moment you do.

| Mirror | What goes there | Secrets needed |
|---|---|---|
| GitHub Pages | The website (`site/`) | none — uses the built-in token |
| Hugging Face Space | The website (`site/`) | `HF_TOKEN`, `HF_SPACE` |
| GitLab | The full repo | `GITLAB_TOKEN`, `GITLAB_REPO` |
| Gitee | The full repo | `GITEE_TOKEN`, `GITEE_USER`, `GITEE_REPO` |

Secrets go in **Settings → Secrets and variables → Actions → New repository
secret**.

---

## GitHub Pages

Nothing to create. One switch: **Settings → Pages → Source → GitHub Actions**.

Until that is set, the workflow succeeds and no site appears, which is a
confusing way to lose an afternoon. Published at
`https://<user>.github.io/clips-studio/`.

## Hugging Face Space

A static Space that serves the same `site/` folder. Worth having because it puts
the project in front of people already searching for local AI tools.

1. Create a Space at <https://huggingface.co/new-space> — **SDK: Static**.
2. Create a token at <https://huggingface.co/settings/tokens> with **write**
   access.
3. Add secrets: `HF_TOKEN` (the token) and `HF_SPACE` (e.g.
   `ColinGPT9/clips-studio`).

`site/README.md` carries the YAML frontmatter that tells Hugging Face this is a
static Space. It is pushed as the Space's root, which is why every path in the
HTML is relative.

**A Space cannot run Clips Studio.** It is a Windows desktop app needing a local
GPU, FFmpeg and your files. The Space is the website, and says so.

## GitLab

1. Create an **empty** project at <https://gitlab.com/projects/new> — no README,
   no licence, or the first push will conflict.
2. Create a personal access token with the `write_repository` scope.
3. Add secrets: `GITLAB_TOKEN` and `GITLAB_REPO` (e.g. `colingpt9/clips-studio`).

## Gitee

Gitee reaches developers in China, where GitHub is slow or unreachable for many
people. It is the fiddliest of the three:

1. **Accounts need real-name verification** (phone number and ID). There is no
   way around this and no way to automate it.
2. The interface is primarily Chinese.
3. New repositories are subject to content review, and pushes can be blocked
   until it passes.
4. There are file-size limits — not a problem here, since the FFmpeg binaries
   and build output are gitignored.

Once you have an account:

1. Create an empty repository.
2. Generate a private token at <https://gitee.com/personal_access_tokens>.
3. Add secrets: `GITEE_TOKEN`, `GITEE_USER` (your Gitee username) and
   `GITEE_REPO` (e.g. `colingpt9/clips-studio`).

---

## These are one-way mirrors

Every job does a `--force` push from GitHub. **A commit made directly on a mirror
will be overwritten on the next push to `main`.** Keep GitHub as the place work
happens, and point contributors there — a note in each mirror's description
saves confusion.
