/** The App Store listing page.
 *
 *  The template shipped invented testimonials here ("CryptoKings … $18,000+/mo").
 *  They are gone rather than adapted: Clips Studio has no success stories to
 *  report yet, and made-up ones on a page selling a tool to people who make
 *  their living from clips would be both dishonest and obvious.
 *
 *  No auth on this surface — it is browsed before installing.
 */

import { Footer, DownloadButton, Hero, Limits, Requirements, Steps } from "@/components/Brand";
import { LINKS, SELLING_POINTS } from "@/lib/content";

export const metadata = {
	title: "Clips Studio — free AI clipping that runs on your own PC",
	description:
		"Turn Twitch, Kick and YouTube VODs into vertical clips with captions, on your members' own machines. Free, open source, no credits and no watermark.",
};

export default function DiscoverPage() {
	return (
		<main className="cs-page">
			<Hero
				eyebrow="Free · open source · AGPL-3.0"
				title="Free AI clipping your members run on their own PC"
			>
				<p className="cs-lede">
					Built for clipping communities. A member pastes a Twitch, Kick or
					YouTube VOD and gets back vertical clips with word-synced
					captions and the speaker kept in frame — no credits to run out
					of, no watermark, and nothing for you to host.{" "}
					<strong>
						Add it to your community and your clippers have it in their
						sidebar.
					</strong>
				</p>
				<div className="cs-cta-row">
					<DownloadButton label="Try it yourself" />
					<a
						className="cs-ghost"
						href={LINKS.github}
						target="_blank"
						rel="noreferrer"
					>
						Read the source
					</a>
				</div>
			</Hero>

			<h2 className="cs-h2">What your members get</h2>
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

			<h2 className="cs-h2">How a member uses it</h2>
			<Steps />

			<h2 className="cs-h2">What their PC needs</h2>
			<Requirements />

			<h2 className="cs-h2">What it does not do</h2>
			<Limits />
			<p className="cs-note" style={{ marginTop: 14 }}>
				<span className="cs-pill">Alpha</span> Clips Studio is early
				software, developed in the open. That is stated here rather than
				discovered later.
			</p>

			<Footer />
		</main>
	);
}
