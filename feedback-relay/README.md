# Feedback relay (Cloudflare Worker)

This tiny worker is what lets Clips Studio users send bug reports and
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

## Abuse response

- Rotate the URL: `name = "..."` in wrangler.toml → deploy → update settings.yaml.
- Rotate the token: GitHub → revoke → `npx wrangler secret put GITHUB_TOKEN`.
- Tighten limits: `MAX_PER_DAY` / `DIFFICULTY_BITS` at the top of worker.js.
- Kill switch: `npx wrangler delete` (the app falls back to save-report-to-file).
