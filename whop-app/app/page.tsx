/** The bare root, reached only by someone hitting the deployment URL
 *  directly — Whop routes real traffic to /experiences/[id], /dashboard/[id]
 *  or /discover. Kept as a signpost rather than a 404 so a stray visit lands
 *  somewhere useful. */

import { Footer, DownloadButton, Hero } from "@/components/Brand";
import { LINKS } from "@/lib/content";

export default function Page() {
	return (
		<main className="cs-page">
			<Hero
				eyebrow="Whop app"
				title="Clips Studio for clipping communities"
			>
				<p className="cs-lede">
					This is the Whop app for{" "}
					<strong>Clips Studio</strong>, free open-source AI clipping that
					runs on your own PC. It is meant to be opened from inside a Whop
					community — if you got here directly, the links below are what
					you are after.
				</p>
				<div className="cs-cta-row">
					<DownloadButton />
					<a
						className="cs-ghost"
						href={LINKS.site}
						target="_blank"
						rel="noreferrer"
					>
						Website
					</a>
				</div>
			</Hero>
			<Footer />
		</main>
	);
}
