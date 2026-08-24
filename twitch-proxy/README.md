# Twitch proxy

A Cloudflare Worker that makes Twitch VODs readable from a browser. It is the
only server the web version has, and it exists for one reason.

## Why

Twitch VODs cannot be read from a browser. `gql.twitch.tv` is fine — it
allows the origin and the `Client-ID` header. Everything after it is not:
`usher.ttvnw.net` sends **no CORS headers on a successful response**, and
neither does the CloudFront CDN behind it, for the manifests or the `.ts`
segments. The browser refuses to hand any of it to the page, which surfaces
as "failed to fetch".

> **The trap, recorded because it caught us once.** Twitch **does** send
> `Access-Control-Allow-Origin: *` on *error* responses. A made-up VOD id
> 403s with permissive headers and reads as proof the path is open. Test CORS
> against a **successful** response, or the errors will lie to you.

No client-side trick fixes this. Something has to fetch on the visitor's
behalf and add the header, so this does exactly that and nothing else — no
transcoding, no caching of media, no storage.

## Why Cloudflare rather than Vercel

**Cloudflare does not bill egress for Workers.** A two-hour VOD is ~190 MB of
audio; that would consume Vercel's entire 100 GB monthly allowance in about
500 runs. Here the bytes are free and only the *request count* is metered.

| | Free | Paid |
|---|---|---|
| Requests | 100,000/day | 10M/month, then $0.30/million |
| Bandwidth | not billed | not billed |
| Cost | $0 | $5/month |

At roughly 750 requests per two-hour VOD (~720 audio segments plus the clip
segments), that is about **130 VODs a day free**, or **13,000 a month** on the
$5 plan. Past that it is about 1,300 more VODs per 30¢. The bill is not what
will limit this.

## Deploy

```bash
cd twitch-proxy
npx wrangler login     # opens a browser, click Allow
npx wrangler deploy    # prints your workers.dev URL
```

Then **set `ALLOWED_ORIGINS` in `wrangler.toml`** to the deployed web app's
origin and deploy again. Leaving it empty means any site may use this worker,
which is somebody else's free bandwidth on your account.

### Entries are patterns, not literals — and they have to be

`ALLOWED_ORIGINS` entries may contain `*`, matching within a single hostname
label. That is not a convenience:

> **Vercel mints a new hostname for every deployment** — previews, and the
> per-deployment URL sitting behind the production alias. A literal list is
> correct only until the next push, at which point Twitch breaks again with
> "This proxy does not accept requests from …". That failure already cost
> several rounds of chasing one origin at a time.

So the shipped value covers the production host *and* the deployment hosts:

```
https://clips-kitty-web.vercel.app,https://clips-kitty-web-*.vercel.app
```

`*` expands to `[^.]*`, never `.*`, so it cannot cross a dot — otherwise
`https://evil.clips-kitty-web-x.vercel.app`, a domain anybody can create, would
match.

**Localhost needs no entry.** Any `localhost` or `127.0.0.1` origin is allowed
in code on any port, because a browser sends the exact origin it is on and
`localhost:3000` does not match a build served on `127.0.0.1:4321`.

**A custom domain** is one more comma-separated entry, then `wrangler deploy`.

Finally, put the worker's URL into `TWITCH_PROXY` in `web/lib/content.ts`.
Until that is set the web app recognises Twitch links and explains that they
are unavailable, rather than offering a button that fails.

## Check it works

```bash
curl "https://clips-kitty-twitch.<your-subdomain>.workers.dev/health?vod=<id>"
```

Use a **current** VOD id — Twitch VODs expire. If every step 404s the id is
stale rather than anything being blocked.

This is the question the whole design hangs on, and it cannot be answered from
a laptop: from a home connection every step already works, which is how the
chain was mapped. Twitch is entitled to refuse datacenter IPs, and
Cloudflare's egress is about as datacenter as it gets — so the test has to run
where the proxy runs.

| Step | Proves |
|---|---|
| `1-gql-token` | Twitch issues a playback token to this IP |
| `2-usher-master` | usher serves the master playlist |
| `3-cdn-playlist` | the CDN serves a media playlist |
| `4-cdn-segment` | the CDN serves actual video bytes |

`"verdict": "WORKS — Twitch serves Cloudflare."` means all four passed.

## Endpoints

| | |
|---|---|
| `GET /master?vod=<id>` | playback token + manifest, rendition URLs rewritten to `/media` |
| `GET /media?u=<url>` | a rendition playlist, segment names rewritten to `/seg` |
| `GET /seg?u=<url>` | one segment, streamed straight through |
| `GET /health[?vod=]` | the four-step check above |

Deliberately **not** a generic `?url=` proxy — that is an open relay, and
anyone who found the URL would get free bandwidth on your account. Instead the
playlists are rewritten so every URL a client follows points back here, and
requests are limited to `ALLOWED_ORIGINS` and to Twitch-owned hostnames.

## Built around the free plan

- **50 subrequests per invocation**, so the worker never fetches and stitches a
  whole VOD. The browser pulls each segment through separately — one upstream
  fetch per request.
- **Streaming, never buffering.** `upstream.body` is handed straight to the
  `Response`, so the worker connects two pipes rather than holding the bytes.
  CPU stays near zero regardless of segment size; buffering would be the
  difference between free and billed.
- Segments are cached for a day (they are immutable once written), manifests
  for five minutes, tokens not at all.

## What this costs in honesty

With this deployed, Twitch links no longer go browser-to-Twitch. They route
through infrastructure the project runs. The web app's claim that nothing of
yours touches our servers still holds for local files and for Kick — but not
for Twitch, and the page says so.

## If it gets abused

The origin check stops a browser being drafted into spending your request
budget; it does not stop someone determined with curl. The upgrade is to sign
the rewritten URLs: HMAC the target in `/master` and `/media`, verify it in
`/seg`, so only URLs this worker emitted are proxyable. That costs a
`wrangler secret put` and about ten lines. It was left out deliberately —
setup steps are a real cost for a free tool — but the design has a place for
it.
