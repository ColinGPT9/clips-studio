# Mirrors

Clips Kitty lives on GitHub, but people look for software in more than one
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

A third Hugging Face repository, `clips-studio-releases`, holds the installer
payload. It is **not** a mirror and not automated — it exists because a GitHub
release asset is capped at 2 GiB and the payload is roughly twice that, so it
is the primary download rather than a copy of one. Uploading is a manual step
in [RELEASING.md](RELEASING.md).

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

**A Space cannot run Clips Kitty.** It is a Windows desktop app needing a local
GPU, FFmpeg and your files. The Space is the website, and says so.

## Why the website is mirrored but the code is not

A GitLab repo mirror was set up here and then removed, and Gitee was considered
and left out. The reasoning is the same for both: a code mirror is a second copy
to keep in step, and it earns nothing unless people are actually looking for the
project there. Nobody was. (Gitee additionally wants real-name verification with
a phone number and ID, and reviews new repositories before pushes go through.)

The website is a different case — it costs one force-push and puts the project
in front of people searching Hugging Face for local AI tools.

If that changes, any git host works the same way: add a job to
[`mirror.yml`](../.github/workflows/mirror.yml) modelled on the Hugging Face one,
push `HEAD:main` with a token instead of pushing `site/`, and keep the "skip if
the secret is missing" guard so the workflow stays harmless for anyone who forks
this repo.

---

## These are one-way mirrors

Every job does a `--force` push from GitHub. **A commit made directly on a mirror
will be overwritten on the next push to `main`.** Keep GitHub as the place work
happens, and point contributors there — a note in each mirror's description
saves confusion.
