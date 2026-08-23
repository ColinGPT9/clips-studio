import type { Metadata } from "next";
import { SITE_URL } from "@/lib/content";
import "./globals.css";

/** Social preview art, absolute and hosted on the GitHub Pages site.
 *
 *  Absolute rather than a local `/mascot.png` on purpose: relative metadata
 *  URLs need `metadataBase`, which means knowing the deployed domain at build
 *  time — and this app is meant to be deployable anywhere without config.
 *  The marketing site already serves this exact file for the same purpose
 *  (see the og:image in site/index.html), so it is one asset, one place. */
const OG_IMAGE = "https://colingpt9.github.io/clips-studio/assets/mascot.png";

const TITLE = "Clips Kitty Web — find your best clips in the browser";
const DESCRIPTION =
	"Paste a Twitch or Kick VOD, or drop in a recording, and get the moments worth clipping — cut and ready to edit. Runs entirely in your browser. Nothing to install, and your video is never uploaded.";

/** Written for the places this link actually gets pasted.
 *
 *  Without these tags a Discord, Reddit or X post renders the URL bare, which
 *  is a poor first impression for anything — and a fatal one for a page whose
 *  next request is "sign in with your OpenRouter account". A preview card is
 *  most of what makes a shared link look like a product rather than spam.
 *
 *  The description leads with what someone gets and closes on the two
 *  objections a creator has about a web tool they have never heard of: does
 *  it need an install, and where does my footage go. */
export const metadata: Metadata = {
	title: TITLE,
	description: DESCRIPTION,
	icons: { icon: "/mascot-head.png" },

	// Indexing is off until `SITE_URL` names a real domain — see the note on
	// that constant. Vercel already sends `X-Robots-Tag: noindex` on preview
	// deployments, but production is exactly the case that needs the guard.
	metadataBase: SITE_URL ? new URL(SITE_URL) : undefined,
	alternates: SITE_URL ? { canonical: SITE_URL } : undefined,
	robots: SITE_URL
		? { index: true, follow: true }
		: { index: false, follow: true },
	openGraph: {
		title: TITLE,
		description: DESCRIPTION,
		siteName: "Clips Kitty",
		type: "website",
		images: [{ url: OG_IMAGE, width: 1024, height: 1024, alt: "Clips Kitty" }],
	},
	twitter: {
		card: "summary_large_image",
		title: TITLE,
		description: DESCRIPTION,
		images: [OG_IMAGE],
	},
};

export default function RootLayout({
	children,
}: Readonly<{ children: React.ReactNode }>) {
	return (
		<html lang="en">
			<body className="antialiased">{children}</body>
		</html>
	);
}
