/** Copy and links for the web tool.
 *
 *  Kept in one place for the same reason `whop-app/lib/content.ts` is: this
 *  page makes claims about software that runs somewhere else, and an
 *  inaccurate one is discovered by the reader when it fails on them.
 *
 *  The version below is pinned to a payload that is actually live on Hugging
 *  Face — see the long note in `whop-app/lib/content.ts`. **These two must
 *  move together.** Pointing at a setup whose payload has not been uploaded
 *  hands every visitor a failed install.
 */

export const VERSION = "1.1.3";

/** The public address this tool is meant to live at — and the single switch
 *  that turns search-engine indexing on.
 *
 *  **Leave empty until a real domain is pointed at the deployment.** While it
 *  is empty the page asks not to be indexed, and that is deliberate rather
 *  than cautious: letting Google index the `*.vercel.app` URL first means
 *  that address accumulates the ranking, and moving to a proper domain later
 *  leaves a duplicate you cannot easily retire. Better to have no index entry
 *  for a week than the wrong one for a year.
 *
 *  Setting it does three things at once — allows indexing, sets the canonical
 *  URL, and fills in the sitemap. No other file needs touching.
 *
 *  Prefer a subdomain or path of whatever domain the marketing site ends up
 *  on (`try.example.com`, or `example.com/try`) rather than a separate
 *  domain. Two domains split the link equity between the page that explains
 *  the product and the page that demonstrates it; one consolidates it.
 *
 *  Annotated `: string` deliberately. Without it TypeScript infers the
 *  literal type `""`, every `if (SITE_URL)` becomes provably false, and the
 *  branches that use it narrow to `never` — the compiler rejects code that is
 *  correct the moment a real value is filled in. */
export const SITE_URL: string = "";

export const LINKS = {
	download: `https://github.com/ColinGPT9/clips-studio/releases/download/v${VERSION}/ClipsKitty-Web-Setup-${VERSION}.exe`,
	site: "https://colingpt9.github.io/clips-studio/",
	github: "https://github.com/ColinGPT9/clips-studio",
	/** Same destination as the desktop app's donate button and the website, so
	 *  there is one place money goes. See DONATE_NOTE for what it funds. */
	donate: "https://paypal.me/clipsstudio",
	openRouterCredits: "https://openrouter.ai/settings/credits",
} as const;

/** The Cloudflare Worker that makes Twitch VODs readable — and the switch
 *  that turns Twitch links on.
 *
 *  **Leave empty until `twitch-proxy/` is deployed.** While it is empty,
 *  Twitch links are recognised and refused with an explanation, which is far
 *  better than offering a button that fails with "failed to fetch".
 *
 *  Why a proxy is needed at all: `usher.ttvnw.net` and Twitch's CDN send no
 *  CORS headers on successful responses, so a browser is not permitted to
 *  read the manifests or the segments. Nothing client-side fixes that. See
 *  `twitch-proxy/README.md`.
 *
 *  Note what this costs in honesty: with it set, Twitch links no longer go
 *  browser-to-Twitch. They route through infrastructure the project runs, and
 *  the "nothing of yours touches our servers" claim holds for local files and
 *  Kick but not for Twitch. The copy on the page says so. */
export const TWITCH_PROXY: string =
	"https://clips-kitty-twitch.clipsstudio.workers.dev";

/** Recommended defaults.
 *
 *  Preferences, not a hardcoded catalogue: the model list is fetched live
 *  because OpenRouter's changes daily and any list baked in here would rot.
 *  If a preference is gone, the picker falls back to the cheapest model that
 *  can actually return JSON. */
export const PREFERRED_TRANSCRIBE_MODEL = "openai/whisper-large-v3-turbo";
export const PREFERRED_SCORE_MODEL = "google/gemini-2.5-flash-lite";

/** What this page deliberately does not do.
 *
 *  This is the honest half, and it is also the pitch: every line is a reason
 *  the desktop app exists. Written as capability gaps rather than as
 *  nagging — someone who only ever needs a landscape cut should be able to
 *  take it and go. */
export const DESKTOP_ONLY: { title: string; body: string }[] = [
	{
		title: "Better clip picking",
		body: "This page reads the transcript and listens for laughter and loud reactions. The desktop app adds what it can see — on-screen motion, scene cuts, who is speaking — plus Twitch chat spikes, and reranks the finalists. The gap is widest on gameplay, where the best moment is something that happens rather than something said.",
	},
	{
		title: "It learns your creators",
		body: "The desktop app builds a profile per creator — running jokes, catchphrases, storylines, collaborators — and remembers which clips you keep, so its picks improve over time. This page starts from nothing every visit.",
	},
	{
		title: "Vertical 9:16 with the speaker kept in frame",
		body: "Subject tracking follows whoever is talking and crops around them. Too heavy for a browser, and the part that turns a clip into a Short.",
	},
	{
		title: "Word-synced captions burned in",
		body: "TikTok, Reels and Shorts do not read subtitle files, so the text has to be rendered into the video itself.",
	},
	{
		title: "Frame-accurate cuts",
		body: "Cuts here land on the nearest keyframe, so a clip can start a second early. The desktop app re-encodes and lands exactly where it should.",
	},
	{
		title: "YouTube by link",
		body: "Twitch and Kick VODs work here, but YouTube's video servers refuse browsers outright. The desktop app takes a link from all three.",
	},
	{
		title: "Local AI, nothing metered",
		body: "Runs the model on your own machine, so there is no per-clip cost and no daily request cap however many hours you get through.",
	},
	// Wording taken from site/index.html's "19 languages" section rather than
	// written here. An earlier version of this entry advertised "queue them
	// straight to YouTube on a schedule" — a feature the maintainer had not
	// shipped, invented by describing code (`core/scheduler.py`) instead of
	// asking what actually works. Uploading needs the user's own Google API
	// credentials and, until their app passes YouTube's audit, lands every
	// upload as private. None of that belongs in a one-line boast.
	{
		title: "19 languages",
		body: "Translate, subtitle or dub a clip, with every line shown for review before anything is burned in. The app itself is translated into all 19 too.",
	},
];

/** The sharpest contrast this page has, so it is not left as a footnote.
 *
 *  This page costs the visitor real credits every time they use it, because
 *  it rents someone else's GPU by the token. The desktop app rents nothing —
 *  the model runs on their own machine — so today it costs nothing to run,
 *  however many hours of VOD they get through. Anyone weighing "should I
 *  bother downloading it" deserves that stated plainly rather than discovered
 *  later.
 *
 *  Present tense on purpose. What the app costs in future is the maintainer's
 *  call, and copy here should describe what is true now rather than commit
 *  them to anything. */
export const DESKTOP_IS_FREE =
	"Clips Kitty for Windows is completely free to run. It is open source, there is no subscription, no credits and no watermark — and because the AI runs on your own PC, clipping a hundred VODs costs exactly nothing. Only this browser version uses paid API credits, because a browser cannot run the model itself.";

/** The donate copy, taken VERBATIM from the desktop app and the website.
 *
 *  `ui/src/renderer/src/pages/Dashboard.tsx` and `site/index.html` already say
 *  this, word for word, and this is the third surface — so it is copied rather
 *  than rewritten. Brand voice is the maintainer's to set, and an earlier
 *  version of this file did not copy it: it invented "free and always will
 *  be", which is a PROMISE ABOUT THE FUTURE nobody had made and which is not
 *  true. Note what the real copy does instead — "keep it free for everyone" is
 *  an aspiration donations support, not a guarantee.
 *
 *  If the wording changes in the app or on the site, change it here too. Do
 *  not improve it here. */
export const DONATE_TITLE = "Clips Kitty is free & open source ❤️";

export const DONATE_NOTE =
	"It runs entirely on your PC with no fees. Please consider donating to help cover development costs and keep it free for everyone.";

/** Said before anyone signs in, because it is the thing people are wary of.
 *
 *  This used to end "we have no server in the middle", which stopped being
 *  true the moment the Twitch proxy went live. Twitch's video servers refuse
 *  browsers outright, so a Twitch VOD is relayed through a Cloudflare Worker
 *  we run. It stores nothing and never sees an OpenRouter key — but "relayed
 *  through our infrastructure" and "never touches our infrastructure" are
 *  different claims, and the smaller one is the true one. Local files and Kick
 *  still never touch us at all. */
export const PRIVACY_NOTE =
	"Your own files never leave your computer — they are read in the browser and never uploaded. Only short audio snippets are sent for transcription, straight to OpenRouter on your own account; we never see your key. Twitch VODs are the one exception: Twitch blocks browsers from reading them, so those are relayed through a small server we run, which stores nothing.";

export const COST_NOTE =
	"You pay OpenRouter directly, at cost. A 30-minute recording is usually a few cents. Transcription needs a funded account — OpenRouter requires at least $0.50 of credit before it will accept audio, so the free daily allowance covers the scoring but not the listening.";
