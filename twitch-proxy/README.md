# Twitch probe

**A throwaway diagnostic, not a feature.** It answers one question, and then
it either becomes the real proxy or gets deleted.

## The question

Twitch VODs cannot be read from a browser. `gql.twitch.tv` is fine, but
`usher.ttvnw.net` returns **no CORS headers on a successful response**, and
neither does the CloudFront CDN behind it — not for the manifests and not for
the `.ts` segments. The browser therefore refuses to hand any of it to the
page, which is why `web/` shows "failed to fetch" on a Twitch link.

> Worth knowing, because it is what made this look fine the first time it was
> checked: Twitch **does** send `Access-Control-Allow-Origin: *` on *error*
> responses. A made-up VOD id 403s with permissive headers and looks like
> proof the path is open. Test CORS against a **successful** response or the
> errors will lie to you.

The only fix is a proxy that fetches on the visitor's behalf and adds the
header. Before building one, there is a prior question that kills the whole
idea if the answer is no:

**Does Twitch serve requests coming from Cloudflare's network at all?**

Twitch is entitled to refuse datacenter IPs, and Cloudflare's egress is about
as datacenter as it gets. This cannot be answered from a laptop — from a home
connection every step already works, which is how the chain was mapped. The
only variable is the source IP, so the test has to run where the proxy would.

## Run it

```bash
cd twitch-proxy
npx wrangler login      # opens a browser, click Allow
npx wrangler deploy     # prints your workers.dev URL
```

Then, with a **current** Twitch VOD id — they expire, so grab one from a
channel's Videos tab:

```bash
curl "https://clips-kitty-twitch-probe.<your-subdomain>.workers.dev/?vod=<id>"
```

## Reading the result

It walks the same four steps the real proxy would and reports each, so a
failure names the step rather than just failing:

| Step | What it proves |
|---|---|
| `1-gql-token` | Twitch issues a playback token to this IP |
| `2-usher-master` | usher serves the master playlist |
| `3-cdn-playlist` | the CDN serves a media playlist |
| `4-cdn-segment` | the CDN serves actual video bytes |

`"verdict": "WORKS — ..."` means all four passed and the proxy is worth
building.

If **every** step 404s, the VOD id is stale rather than Cloudflare being
blocked. Retry with a current one before concluding anything.

## If it works

Roughly what the real proxy would cost, so the decision is made with numbers:

- audio-only for a 2-hour VOD is ~190 MB and ~720 segment requests
- clip cutting adds only the segments covering chosen moments
- Cloudflare Workers' free tier is 100,000 requests/day — about 130 VODs —
  and does not meter egress the way Vercel does

## Then delete it

This worker has **no authentication and no rate limiting**. It is a
ten-minute experiment, and an open proxy left running is someone else's free
bandwidth. Replace it with the real thing or take it down.
