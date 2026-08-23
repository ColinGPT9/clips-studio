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
	{
		title: "19 languages, scheduling and uploads",
		body: "Translate, subtitle and dub clips, then queue them straight to YouTube on a schedule.",
	},
];

/** The sharpest contrast this page has, so it is not left as a footnote.
 *
 *  This page costs the visitor real credits every time they use it, because
 *  it rents someone else's GPU by the token. The desktop app rents nothing —
 *  the model runs on their own machine — so it is free to run, forever, with
 *  no cap on how many hours of VOD they get through. Anyone weighing "should
 *  I bother downloading it" deserves that stated plainly rather than
 *  discovered later. */
export const DESKTOP_IS_FREE =
	"Clips Kitty for Windows is completely free to run. It is open source, there is no subscription, no credits and no watermark — and because the AI runs on your own PC, clipping a hundred VODs costs exactly nothing. Only this browser version uses paid API credits, because a browser cannot run the model itself.";

/** What the donate button is actually for.
 *
 *  A "Donate" button beside a free app reads as a price tag with extra steps
 *  unless it says otherwise. Nothing here is paywalled, there is no upgrade,
 *  and the money is not income — it pays people to fix issues. Worth one
 *  sentence, because the alternative is that some readers quietly assume the
 *  free version is crippled. */
export const DONATE_NOTE =
	"Clips Kitty is free and always will be — there is no paid tier and nothing is held back. Donations go towards paying people to fix bugs and build features, not to the maintainer.";

/** Said before anyone signs in, because it is the thing people are wary of. */
export const PRIVACY_NOTE =
	"Your recording never leaves your computer — it is read in the browser and never uploaded to us. Only short audio snippets are sent, and they go directly to OpenRouter on your own account. We have no server in the middle and never see your key.";

export const COST_NOTE =
	"You pay OpenRouter directly, at cost. A 30-minute recording is usually a few cents. New OpenRouter accounts also get 50 free requests a day, which covers a short recording outright.";
