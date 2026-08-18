# The Clips Studio Whop app

A marketing surface that puts Clips Studio in front of the people who would
use it: members of clipping communities on [Whop](https://whop.com), who cut
streamer VODs into vertical shorts and get paid per view.

**It is a set of pages, nothing more.** It does not process video, store
anything, talk to a member's PC, or connect to the Clips Studio engine. The
desktop app has no idea this exists and works exactly the same without it.

## What it is

Whop embeds the app in an iframe and routes to three surfaces:

| Route | Who sees it | What it says |
|---|---|---|
| `/experiences/[experienceId]` | **members**, in the community sidebar | what Clips Studio does, what their PC needs, the download, how it works, what it does not do |
| `/dashboard/[companyId]` | the **community owner**, in their dashboard | why it is worth recommending, what members need, and a message they can paste into their community |
| `/discover` | anyone browsing the **App Store** | the listing pitch |

Every claim on all three comes from [`lib/content.ts`](lib/content.ts), which
is the only place to edit copy. It is written against the main repo's
`README.md` and `site/`, so the app cannot quietly start promising something
the software does not do.

## Running it

```bash
npm install
npm run dev      # starts Whop's dev proxy + next dev
```

`npm run dev` runs behind `whop-proxy`, which replicates the production
authentication and iframe behaviour. **The member and owner pages will not
render outside it** — they call `verifyUserToken` and fail closed without a
Whop-issued token. That is deliberate. `/discover` and `/` are static and
work anywhere.

### Environment

Create `.env.local` (git-ignored) with the real values from the Whop
dashboard:

```
NEXT_PUBLIC_WHOP_APP_ID=...
WHOP_API_KEY=...
WHOP_WEBHOOK_SECRET=...
```

`.env.development` is committed and holds **placeholders only**. Never put a
real key in it — it is tracked, and `WHOP_API_KEY` is a secret that can act
as your app.

## Deploying

Hosted on Vercel's free tier; Whop only needs a public HTTPS URL.

```bash
npx vercel --prod
```

Then in the Whop dashboard set the app's base URL to the deployment, and the
view paths to `/experiences/[experienceId]` and `/dashboard/[companyId]`.
Put the three environment variables into Vercel's project settings — not into
a committed file.

## Getting it in front of a community

1. Deploy, then install the app into your own test whop and click through
   both views.
2. **Share the install link directly with a community owner.** This works
   without an App Store listing, and is the fastest route to a specific
   community.
3. Submit to the App Store in parallel. It is free — Whop's marketplace fee
   is 0% — and reviewed in a few days. The bar is that the app works end to
   end in production without bugs.

## Editing the copy

Change [`lib/content.ts`](lib/content.ts). The three pages read from it, so
they cannot disagree with each other.

Two rules that matter more than they look:

- **Requirements go above the download button.** 16 GB of RAM is not a
  suggestion — on 8 GB the engine analyses an entire video and then fails at
  the render stage, which reads as a crash rather than a limit. Someone who
  learns that after a 5 GB download files a bug; someone who reads it first
  does not.
- **Do not add a claim that is not true of an installed build.** No cloud or
  external AI (Ollama only), no AI dubbing (Piper is deliberately not
  bundled), no macOS, no live stream capture.
