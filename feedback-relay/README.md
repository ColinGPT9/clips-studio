# Feedback relay (Cloudflare Worker)

This tiny worker is what lets Clips Kitty users send bug reports and
feature requests **without a GitHub account**. The app posts the report
here; the worker checks it (proof-of-work challenge, per-IP daily limit,
size caps, field validation, honeypot) and files it as a GitHub Issue
using a token that only exists in the worker's secrets — never in the app.

## One-time setup (~10 minutes, free)

1. **Cloudflare account** — sign up free at https://dash.cloudflare.com/sign-up
   (no domain or credit card needed; Workers free tier is 100k requests/day).

2. **GitHub token** — github.com → Settings → Developer settings →
   Fine-grained personal access tokens → Generate new token:
   - Repository access: *Only select repositories* → `clips-studio`
   - Permissions → Repository → **Issues: Read and write**
     and **Contents: Read and write** (Contents is only for screenshot
     uploads to the `feedback-assets` branch — skip it to disable those).
   - Expiration: 1 year (set a reminder to rotate).

3. **Screenshots branch** — `feedback-assets` (already created and pushed
   for this repo; for a fork: `git switch --orphan feedback-assets`,
   empty commit, push).

4. **Deploy** (from this folder):
   ```
   npx wrangler login                          # opens browser, click Allow
   npx wrangler kv namespace create FEEDBACK_KV
   #   -> paste the printed id into wrangler.toml
   npx wrangler secret put GITHUB_TOKEN        # paste the token from step 2
   npx wrangler secret put HMAC_KEY            # paste any long random string
   npx wrangler deploy
   ```
   The deploy prints your URL, e.g.
   `https://clips-studio-feedback.<your-subdomain>.workers.dev`

5. **Point the app at it** — in `config/settings.yaml`:
   ```yaml
   feedback:
     relay_url: https://clips-studio-feedback.<your-subdomain>.workers.dev
   ```
   Commit that change so every user's app knows where to send feedback.

## Whose name the issues appear under

**Whoever owns `GITHUB_TOKEN` is recorded by GitHub as the author of every
issue this relay files.** With a personal PAT that means user-written bug
reports show up authored by the maintainer, appearing overnight under his name
as though he wrote them. Issues #81 and #82 are exactly that.

Two layers deal with it, and only one of them actually changes the author:

**1. The attribution banner (already in `worker.js`).** Every filed issue opens
with a line saying a user submitted it through the in-app feedback hub and the
maintainer did not write it. Costs nothing and needs no account, but the GitHub
author field still reads as the token owner.

**2. A machine account, which is the real fix.** Create a second GitHub account
for the bot, give it write access to this repo only, and put ITS token in the
secret:

```bash
# as the bot account: Settings -> Developer settings -> fine-grained PAT
#   repository access: ColinGPT9/clips-studio only
#   permissions: Issues Read+Write, Contents Read+Write (screenshots branch)
npx wrangler secret put GITHUB_TOKEN     # paste the BOT's token
npx wrangler deploy
```

Issues then appear from the bot, which is what a reader expects of an automated
relay.

**Name it as a bot** — `clips-kitty-bot` or similar, with a profile that says
what it is. The goal is accurate attribution: these reports genuinely are not
the maintainer's, and a clearly-labelled machine account says so. A second
account posing as a *person* would be the opposite, and would breach GitHub's
terms besides.

## Abuse response

- Rotate the URL: `name = "..."` in wrangler.toml → deploy → update settings.yaml.
- Rotate the token: GitHub → revoke → `npx wrangler secret put GITHUB_TOKEN`.
- Tighten limits: `MAX_PER_DAY` / `DIFFICULTY_BITS` at the top of worker.js.
- Kill switch: `npx wrangler delete` (the app falls back to save-report-to-file).
