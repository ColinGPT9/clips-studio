/** The full marketing page.
 *
 *  Used by every route a person can reach without a Whop session: the
 *  deployment root, the App Store discover page, and the fallback shown when
 *  an embedded view is opened outside Whop.
 *
 *  All three used to say some version of "this is a Whop app, you are in the
 *  wrong place", which is a description of the plumbing rather than a reason
 *  to care. Anyone who lands on one of these is a potential user — including
 *  a community owner following a link you sent them — so they get the pitch
 *  and a download button, and the Whop-specific detail is a footnote.
 */

import { Footer, DownloadButton, StoreButton, Hero, Limits, Requirements, Steps } from "@/components/Brand";
import { DOWNLOAD_NOTE, LINKS, SELLING_POINTS } from "@/lib/content";

export function Pitch({ note }: { note?: string }) {
	return (
		<main className="cs-page">
			<Hero
				eyebrow="Free · open source · runs on your PC"
				title="Turn a three-hour VOD into clips worth posting"
			>
				<p className="cs-lede">
					Paste a link to a Twitch VOD, a Kick VOD or a YouTube video.
					Clips Kitty watches the whole thing, picks the moments worth
					posting, crops them to a phone screen with the speaker kept in
					frame, burns in word-synced captions and writes the titles.{" "}
					<strong>No credits, no watermark, no subscription.</strong>
				</p>
				<div className="cs-cta-row">
					<DownloadButton />
					<StoreButton />
					<a
						className="cs-ghost"
						href={LINKS.site}
						target="_blank"
						rel="noreferrer"
					>
						See what it looks like
					</a>
				</div>
				<p className="cs-note">{DOWNLOAD_NOTE}</p>
			</Hero>

			<h2 className="cs-h2">Why clippers use it</h2>
			<div className="cs-grid">
				{SELLING_POINTS.map((p) => (
					<div className="cs-card" key={p.title}>
						<p style={{ margin: 0, fontWeight: 600, fontSize: 15 }}>
							{p.title}
						</p>
						<p className="cs-note" style={{ marginTop: 6 }}>
							{p.body}
						</p>
					</div>
				))}
			</div>

			<h2 className="cs-h2">How it works</h2>
			<Steps />

			<h2 className="cs-h2">What your PC needs</h2>
			<Requirements />

			<h2 className="cs-h2">What it does not do</h2>
			<Limits />

			<h2 className="cs-h2">Free, and staying that way</h2>
			<div className="cs-card">
				<p style={{ margin: 0, fontSize: 14.5, lineHeight: 1.6 }}>
					Clips Kitty runs entirely on your own PC and charges nothing —
					no credits, no seats, no cut of what you earn from your clips.
					If it saves you time, a donation helps cover development and
					keeps it free for everyone.
				</p>
				<div className="cs-cta-row" style={{ marginTop: 14 }}>
					<a
						className="cs-ghost"
						href={LINKS.donate}
						target="_blank"
						rel="noreferrer"
					>
						Donate to Clips Kitty
					</a>
				</div>
				<p className="cs-note" style={{ marginTop: 10 }}>
					Entirely optional. Nothing is locked behind it and there is no
					paid version.
				</p>
			</div>

			{note ? <p className="cs-note" style={{ marginTop: 26 }}>{note}</p> : null}

			<Footer />
		</main>
	);
}
