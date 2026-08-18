/** Every factual claim the app makes, in one place.
 *
 *  This app is marketing for software that runs on someone else's PC, which
 *  makes an inaccurate claim here expensive: the reader finds out by having
 *  it fail. Each item below is copied from the main repo rather than
 *  written fresh — README.md's Requirements and Supported platforms
 *  tables, and site/twitch.html's "What it does not do".
 *
 *  Deliberately ABSENT, because none of it is true of an installed build:
 *  cloud or external AI APIs (Ollama only), AI dubbing (Piper is not
 *  bundled), macOS, live stream capture, any hosted or multi-user service.
 */

/** The version this app sends people to.
 *
 *  PINNED ON PURPOSE — do not change it to "whatever is newest".
 *
 *  The Web Setup on GitHub is only ~800 KB. During install it fetches
 *  `clips-studio-<version>-x64.nsis.7z` **by name** from the Hugging Face
 *  repo, because a GitHub release asset is capped at 2 GiB and the payload
 *  is 5.88 GiB. So the setup and the payload are a matched pair: pointing
 *  at a setup whose payload has not been uploaded yet gives every person
 *  who clicks a failed install.
 *
 *  When a new version ships, this changes ONLY after its payload is live on
 *  Hugging Face. See docs/RELEASING.md in the main repo.
 *
 *  Verified 2026-08-18: the newest payload on Hugging Face is
 *  `clips-studio-0.1.2-x64.nsis.7z`, so 0.1.2 is what this must point at —
 *  not whatever GitHub happens to have newest. One version everywhere.
 */
export const VERSION = "0.1.2";

export const LINKS = {
	/** Straight to the installer for VERSION, so a clipper gets one click
	 *  rather than a list of three alpha releases to choose between.
	 *  Deliberately not /releases/latest: every release is flagged as a
	 *  prerelease, so GitHub's "latest" is undefined — the API 404s and the
	 *  web URL silently redirects to the full list. */
	download: `https://github.com/ColinGPT9/clips-studio/releases/download/v${VERSION}/ClipsStudio-Web-Setup-${VERSION}.exe`,
	site: "https://colingpt9.github.io/clips-studio/",
	/** Same destination as the desktop app's donate button and the website,
	 *  so there is one place money goes. */
	donate: "https://paypal.me/clipsstudio",
	github: "https://github.com/ColinGPT9/clips-studio",
	issues: "https://github.com/ColinGPT9/clips-studio/issues",
	privacy: "https://colingpt9.github.io/clips-studio/privacy.html",
} as const;

export const REQUIREMENTS: { label: string; value: string; note?: string }[] = [
	{
		label: "OS",
		value: "Windows 10 or 11",
		note: "No Mac build exists yet.",
	},
	{
		label: "Memory",
		value: "16 GB of RAM",
		note: "Not a suggestion. On 8 GB it analyses the whole video and then renders nothing, which looks like a crash.",
	},
	{
		label: "Graphics",
		value: "NVIDIA GPU recommended",
		note: "It runs on the processor without one, just slowly.",
	},
	{
		label: "Disk",
		value: "About 20 GB free",
		note: "Plus room for the videos you clip.",
	},
];

export const STEPS: string[] = [
	"Install Clips Studio and let it pick an AI model that fits your graphics card.",
	"Paste a link to a Twitch VOD, a Kick VOD or a YouTube video.",
	"Leave it running. It watches the whole thing, picks the moments, crops them vertical with the speaker kept in frame, and burns in word-synced captions.",
	"Review the clips, fix anything the AI got wrong in the editor, and export.",
];

export const SELLING_POINTS: { title: string; body: string }[] = [
	{
		title: "No credits, no watermark",
		body: "Free and unlimited, however many hours of VOD you get through. Nothing is metered and nothing is stamped on your clips.",
	},
	{
		title: "Runs on your own PC",
		body: "The VOD is never uploaded anywhere. No account, no API key, no subscription.",
	},
	{
		title: "Made for long streams",
		body: "Built to sit on a three-hour VOD and find what is worth posting, rather than to trim something you already chose.",
	},
	{
		title: "Open source",
		body: "AGPL-3.0. You can read exactly what it does, and change it.",
	},
];

/** The honest half. This is what makes the rest believable to an audience
 *  that has been sold to before. */
export const LIMITS: { lead: string; body: string }[] = [
	{
		lead: "Live streams are not supported",
		body: "by design — it works on finished VODs, where the whole thing can be studied before deciding what matters.",
	},
	{
		lead: "Windows only",
		body: "for now. The engine should run on Linux and macOS, but no Mac build exists and the maintainer has no Mac to test one on.",
	},
	{
		lead: "It does not post for you",
		body: "Clips land in a folder, organised by creator and video, ready to upload wherever you like.",
	},
	{
		lead: "It is alpha",
		body: "and moves fast. Things break; there is a place to report them.",
	},
];

export const PLATFORMS = ["Twitch VODs", "Kick VODs", "YouTube"] as const;

/** What actually happens when they click download.
 *
 *  The file itself is under a megabyte and then pulls ~6 GB, which is a
 *  surprise worth having in advance — someone on a phone tether or a metered
 *  connection needs to know before they start, not 4 GB in. */
export const DOWNLOAD_NOTE = `Version ${VERSION}. The setup is under 1 MB and then downloads about 6 GB — the engine, FFmpeg, the AI runtime and the tracking and transcription models. That is everything; there is nothing else to install afterwards.`;

/** Paste-ready text for a community owner to post to their members. Kept
 *  short enough to survive a Discord or Whop chat message, and honest
 *  about the hardware so their members do not bounce off a failed install. */
export const OWNER_BLURB = `Clips Studio — free, open-source AI clipping that runs on your own PC.

Paste a Twitch, Kick or YouTube VOD and it finds the moments worth posting, crops them vertical with the speaker kept in frame, burns in word-synced captions and writes titles. No credits, no watermark, no subscription, and the video never leaves your machine.

Needs Windows and 16 GB of RAM (an NVIDIA GPU makes it much faster). Live streams are not supported — finished VODs only.

Download: ${LINKS.download}`;
