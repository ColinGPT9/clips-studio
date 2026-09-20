# Publishing to social media platforms

Clips Kitty can send a finished clip to YouTube, TikTok, Instagram, Facebook,
X, Threads, LinkedIn, Pinterest and Bluesky **in one upload**, through a
service called [Upload-Post](https://www.upload-post.com/).

It is off until you turn it on, and it needs an account with one of two
services — **WoopSocial** (the one to start with) or **Upload-Post**. Both
are bring-your-own-key:
Clips Kitty does not resell, proxy or subsidise anything, and holds no shared
key for either. Your account, your connected socials, your allowance.

## Which one

**Start with WoopSocial.** Its free plan covers most creators outright:
unlimited posts on two connected accounts, with API access. Upload-Post is
there for what WoopSocial does not do.

| | WoopSocial | Upload-Post |
|---|---|---|
| Free plan | **Unlimited posts, 2 accounts**, API included | 10 uploads a month, no TikTok |
| Paid from | $19/month, 20 accounts | $24/month ($16 annual) |
| Destinations | 8 | 9, including **Bluesky** |
| Thumbnails | not through their API | YouTube, LinkedIn, Facebook video |
| First comment | no | yes |
| Posting queue | scheduling only | yes |
| Retry failed platforms | re-publish instead | yes |
| Clip size | **100 MB** in one upload | per-platform limits |

**Reach for Upload-Post if** you need Bluesky, a custom thumbnail, a first
comment, or a posting queue. Otherwise WoopSocial is the simpler and cheaper
route.

With both switched on, the Publish tab gets a **Through** switch at the top so
you pick per clip. It starts on WoopSocial.

---

## Contents

- [Three things, kept separate](#three-things-kept-separate)
- [What leaves your computer](#what-leaves-your-computer)
- [Setting it up](#setting-it-up)
- [Publishing one clip](#publishing-one-clip)
- [Publishing a batch](#publishing-a-batch)
- [Titles, descriptions and hashtags](#titles-descriptions-and-hashtags)
- [Per-platform wording](#per-platform-wording)
- [Thumbnails and covers](#thumbnails-and-covers)
- [First comments](#first-comments)
- [Scheduling and the queue](#scheduling-and-the-queue)
- [Watching what happened, and retrying](#watching-what-happened-and-retrying)
- [What each platform supports](#what-each-platform-supports)
- [Cost and limits](#cost-and-limits)
- [Where your API key is kept](#where-your-api-key-is-kept)
- [The assistant](#the-assistant)
- [When something goes wrong](#when-something-goes-wrong)

---

## Three things, kept separate

It is worth being clear about who does what, because three different accounts
are involved and only one of them is ours.

| | What it is | What it holds |
|---|---|---|
| **Clips Kitty** | This app, on your PC | Your videos, clips and settings |
| **Upload-Post** | A third-party service you sign up to | The connections to your social accounts |
| **Your social accounts** | YouTube, TikTok, Instagram… | Your actual posts |

Clips Kitty never sees a social media password. You link those accounts on
Upload-Post's own page, in your own browser.

## What leaves your computer

Everything else in Clips Kitty runs locally — transcription, moment detection,
tracking, captions, rendering. This feature does not, and cannot.

> When you publish this way, **the clip file and its title, description,
> hashtags and settings are sent to Upload-Post**, which delivers them to the
> platforms you picked.

That is the trade. Nothing is sent unless you press Publish, and the feature
makes no network calls at all while it is switched off.

## Setting it up

**Settings → Publish through WoopSocial → On.** (Or **Upload-Post** further down, if you are using that one.)

1. **Create an Upload-Post account** and connect your social accounts there.
   The button in Settings opens their site in your browser.
2. **Copy your API key** from their dashboard.
3. **Paste it into Clips Kitty** and press *Save and check*.

If you copied the key just before switching back, Clips Kitty notices and
offers it — one click instead of finding the box and pasting. It only ever
reads something that looks like a key, never other clipboard contents, and it
offers rather than applies.

The key is checked against Upload-Post before it is accepted. A key that does
not work is discarded rather than left looking connected.

Then press **Connect your social accounts**. That opens Upload-Post's own
page, where you link YouTube, TikTok and the rest. The page is good for 48
hours. Come back to Clips Kitty and it updates on its own — there is nothing
to press.

### Why it opens a browser instead of a window inside the app

Google blocks OAuth sign-in inside embedded browser windows, and there is no
setting that turns that off. An in-app window would fail on YouTube, which is
usually the account people most want. Your real browser is also where you are
already signed in to these services.

### The profile name

Every upload names an Upload-Post "profile", which is the thing your social
accounts hang off. Clips Kitty fills in `clips-kitty` and you can ignore it.
It only matters if you keep separate sets of accounts, in which case change it
under *Profile name*.

## Publishing one clip

Open a clip in the editor and choose the **Publish** tab.

1. Pick platforms, or press **Everywhere**.
2. Check the title, description and hashtags — they come from the clip.
3. Press **Publish**.

One upload happens, and Upload-Post distributes it. You do not upload the clip
once per platform.

Platforms you have not connected are shown dimmed with a `○`. If you pick one
anyway it comes back as *skipped*, not failed.

Publishing is blocked while you have unsaved edits, because the rendered file
is what gets uploaded. Apply your edits first.

## Publishing a batch

**Clip Editor → Publish all.** It asks before doing anything, and tells you
exactly how many posts that means — twelve clips across four platforms is
forty-eight posts.

Leave **Space them out** on. Posting a dozen clips at the same moment reads as
spam to every platform involved and spends the per-account daily limit at
once. The spacing uses Upload-Post's scheduler, so it carries on after you
close Clips Kitty.

*Publish all* is not the same button as *Export all* next to it. Export writes
files to a folder and you can delete them. This posts publicly and cannot be
undone.

## Titles, descriptions and hashtags

Written once, in the Publish tab, and used everywhere. Each clip starts with
the title, description and hashtags Clips Kitty generated for it.

Anything in **Settings → Added to every description** is appended to every
post — links to your Twitch, your Discord, whatever should always be there.

Hashtags are typed as one line (`#gaming #twitch #clips`) and sent per
platform.

## Per-platform wording

Under **Per-platform wording**, each selected platform folds open with its own
title, description and first comment. Fill one in and it overrides the common
text for that platform only — an Instagram caption cannot change your YouTube
title.

Leave them blank and every platform uses the common text.

## Thumbnails and covers

Tick **Use the thumbnail chosen for this clip** to send the thumbnail you
picked in the editor.

Only **YouTube, LinkedIn and Facebook (video posts)** accept a custom
thumbnail through Upload-Post. The others use a frame from the video. Clips
Kitty says so under each platform rather than letting the option appear to
work and do nothing.

## First comments

Some platforms take a comment posted under the clip — useful for the links
that suppress reach in a caption.

Supported on YouTube, TikTok, Instagram, Facebook, X, Threads, LinkedIn and
Bluesky. **Not on Pinterest.**

## Scheduling and the queue

Three choices under **When**:

- **Publish now.**
- **Schedule** — pick a date and time. Your time zone goes with it, because
  Upload-Post assumes UTC otherwise and your 7pm would move. Up to a year ahead.
- **Add to queue** — the next free slot of the posting queue you set up on
  Upload-Post. They cannot be combined with a specific time.

## Watching what happened, and retrying

After publishing, **Where it went** lists each platform:

```
✓ YouTube     Published        [Open ↗]
✓ TikTok      Published        [Open ↗]
⏳ Instagram   Processing
✗ X           Expired token
○ Pinterest   Not connected
```

It updates on its own until everything settles. Links appear only where
Upload-Post actually returned one.

**Retry the ones that failed** re-runs only the failures, through
Upload-Post's own retry, which reuses the clip it already has. It does not
upload again, so the platforms that worked cannot end up posted twice.

## What each platform supports

Not every platform takes every field. Clips Kitty hides controls a platform
does not support rather than letting them silently do nothing.

| Platform | Separate description | Thumbnail | First comment | AI disclosure | Needs |
|---|---|---|---|---|---|
| YouTube | yes | yes | yes | yes | — |
| TikTok | no (caption only) | no | yes | yes | paid Upload-Post plan |
| Instagram | no (caption only) | no (cover) | yes | yes | — |
| Facebook | yes | yes (video posts) | yes | yes | **page ID** |
| X | no | no | yes | yes | — |
| Threads | no | no | yes | no | — |
| LinkedIn | yes | yes | yes | no | — |
| Pinterest | yes | no (cover) | no | yes | **board ID** |
| Bluesky | no | no | yes | no | — |

**Facebook needs a page ID and Pinterest needs a board ID.** Clips Kitty asks
for them under *Per-platform wording* and refuses to publish without them,
because Upload-Post only reports that failure after the video has been sent.

## Cost and limits

Upload-Post's plans are theirs, and change; check
[their pricing](https://www.upload-post.com/pricing-comparison/) rather than
trusting a number written here.

At the time of writing, the free plan is **10 uploads a month** and does
**not** include TikTok — that needs a paid plan. Clips Kitty charges nothing
for any of this and never pays for your usage.

Each social account also has its own daily limit at Upload-Post's end, which
is a large part of why spacing a batch out matters.

## Where your API key is kept

- Stored through the same encrypted store as your YouTube credentials. On
  Windows that is **DPAPI**, which ties it to your Windows account.
- It lives in `data/credentials/`, not in `settings.yaml`, so it cannot end up
  in a config file pasted into a bug report.
- **No route ever returns it.** The app is told whether a key exists and its
  last four characters, nothing more.
- It is never logged, never sent with a bug report, and never given to the
  local AI model.

Remove it any time with **Remove** next to the key in Settings.

## The assistant

The in-app assistant can tell you whether publishing is set up
(`uploadpost_status`) and report where a clip ended up
(`uploadpost_result`).

It **cannot publish.** Posting to several public platforms at once cannot be
taken back, so the model is never offered that tool — a person presses the
button. This is the same rule the YouTube batch upload follows.

## When something goes wrong

**"Upload-Post did not accept that API key."** The key is wrong or has been
rotated. Copy it again from their dashboard.

**A platform comes back "Not connected".** You picked a platform that is not
linked to your Upload-Post profile. Press *Connect your social accounts*.

**"TikTok uploads are not available on the Free plan."** Exactly what it says;
TikTok needs a paid Upload-Post plan.

**"Your Upload-Post upload allowance for this month is used up."** You have
hit the monthly cap on their plan.

**Nothing happens when a link is clicked.** Clips Kitty only opens
allow-listed addresses in your browser. If Upload-Post changes domain this has
to be updated in `ui/src/main/index.ts`.

**A publish sits on "Processing" for a long time.** Large videos take a while
at their end, and Clips Kitty stops watching after a few minutes. The upload
is not lost — check the platform, or reopen the clip's Publish tab.
