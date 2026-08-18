/** The owner view: what a clipping community owner sees in their dashboard.
 *
 *  Shorter than the member view and pointed at one decision — is this worth
 *  putting in front of my members? So it leads with what they get, states
 *  the hardware gate plainly (their members bouncing off a failed install
 *  reflects on them, not just on us), and ends with a message they can post
 *  without writing anything.
 */

import { Footer, Hero, Limits, Requirements } from "@/components/Brand";
import { CopyBlurb } from "@/components/CopyBlurb";
import { Pitch } from "@/components/Pitch";
import { verifiedUserId } from "@/lib/auth";
import { LINKS } from "@/lib/content";
import { whop } from "@/lib/whop-sdk";

export default async function DashboardPage({
	params,
}: {
	params: Promise<{ companyId: string }>;
}) {
	const { companyId } = await params;

	const userId = await verifiedUserId();
	if (!userId) return <Pitch note="Opened outside a Whop community, so this is the public version of the page. Everything you need is above." />;

	const company = await whop().companies.retrieve(companyId);

	return (
		<main className="cs-page">
			<Hero
				eyebrow="For community owners"
				title="Give your clippers a free tool that does the tedious part"
			>
				<p className="cs-lede">
					Clips Studio is free, open-source AI clipping that runs on a
					member's own PC. It takes a Twitch, Kick or YouTube VOD and
					returns vertical clips with word-synced captions and the speaker
					kept in frame.{" "}
					<strong>
						Nothing to buy, no seats, no per-member cost to
						{company?.title ? ` ${company.title}` : " you"}.
					</strong>
				</p>
			</Hero>

			<h2 className="cs-h2">Why it is worth recommending</h2>
			<div className="cs-grid">
				<div className="cs-card">
					<p style={{ margin: 0, fontWeight: 600, fontSize: 15 }}>
						More clips per member
					</p>
					<p className="cs-note" style={{ marginTop: 6 }}>
						Finding the moments in a three-hour VOD is the slow part.
						Members who are paid per view get through more source
						material.
					</p>
				</div>
				<div className="cs-card">
					<p style={{ margin: 0, fontWeight: 600, fontSize: 15 }}>
						Nothing metered
					</p>
					<p className="cs-note" style={{ marginTop: 6 }}>
						No credits to run out of mid-month and no watermark, so a
						member's output is not capped by what they can afford.
					</p>
				</div>
				<div className="cs-card">
					<p style={{ margin: 0, fontWeight: 600, fontSize: 15 }}>
						Nothing for you to run
					</p>
					<p className="cs-note" style={{ marginTop: 6 }}>
						Each member installs it on their own machine. There is no
						server to host, no account system and no data of yours
						involved.
					</p>
				</div>
				<div className="cs-card">
					<p style={{ margin: 0, fontWeight: 600, fontSize: 15 }}>
						You can read the code
					</p>
					<p className="cs-note" style={{ marginTop: 6 }}>
						AGPL-3.0 and developed in the open, so recommending it does
						not mean vouching for a black box.
					</p>
				</div>
			</div>

			<h2 className="cs-h2">What your members need</h2>
			<p className="cs-note" style={{ marginBottom: 12 }}>
				Worth reading before you post about it. Members below this will
				have a bad time, and the failure is confusing rather than obvious.
			</p>
			<Requirements />

			<h2 className="cs-h2">Be straight with them about this</h2>
			<Limits />

			<h2 className="cs-h2">A message you can post</h2>
			<CopyBlurb />

			<h2 className="cs-h2">If your members want more</h2>
			<p className="cs-note">
				Clips Studio runs a local HTTP API, so a developer in your
				community can build on it — a Discord bot that clips on command, a
				batch runner, a dashboard. It is documented at{" "}
				<a
					href={`${LINKS.github}/blob/main/docs/API.md`}
					target="_blank"
					rel="noreferrer"
					style={{ color: "var(--cs-accent)" }}
				>
					docs/API.md
				</a>
				. If something your community needs is missing, opening an issue is
				the fastest way to get it.
			</p>

			<Footer />
		</main>
	);
}
