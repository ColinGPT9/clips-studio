/** The member view: what a clipper sees in their community's sidebar.
 *
 *  One screen, in the order the reader actually needs it — what it is, then
 *  whether their PC can run it, THEN the download. The requirements come
 *  before the button on purpose.
 */

import {
	Footer,
	DownloadButton,
	Hero,
	Limits,
	OpenInWhop,
	Requirements,
	Steps,
} from "@/components/Brand";
import { PcCheck } from "@/components/PcCheck";
import { verifiedUserId } from "@/lib/auth";
import { DOWNLOAD_NOTE, LINKS, PLATFORMS, SELLING_POINTS } from "@/lib/content";
import { whop } from "@/lib/whop-sdk";

export default async function ExperiencePage({
	params,
}: {
	params: Promise<{ experienceId: string }>;
}) {
	await params;

	// Verify the Whop-issued token before touching the Whop API. Without a
	// valid one we show the public notice and make no API calls at all.
	const userId = await verifiedUserId();
	if (!userId) return <OpenInWhop />;

	const user = await whop().users.retrieve(userId);
	const firstName = (user.name || user.username || "").split(" ")[0];

	return (
		<main className="cs-page">
			<Hero
				eyebrow="Free · open source · runs on your PC"
				title="Turn a three-hour VOD into clips worth posting"
			>
				<p className="cs-lede">
					{firstName ? `${firstName} — p` : "P"}aste a link to a{" "}
					{PLATFORMS.join(", ").replace(/, ([^,]*)$/, " or $1")} video and
					Clips Studio watches the whole thing, picks the moments worth
					posting, crops them to a phone screen with the speaker kept in
					frame, burns in word-synced captions and writes the titles.{" "}
					<strong>No credits, no watermark, no subscription.</strong>
				</p>
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

			<h2 className="cs-h2">Check this before you download</h2>
			<Requirements />
			<PcCheck />

			<div className="cs-cta-row">
				<DownloadButton />
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

			<h2 className="cs-h2">How it works</h2>
			<Steps />

			<h2 className="cs-h2">What it does not do</h2>
			<Limits />

			<Footer />
		</main>
	);
}
