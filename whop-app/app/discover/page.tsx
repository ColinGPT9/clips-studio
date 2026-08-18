/** The App Store listing page.
 *
 *  The template shipped invented testimonials here ("CryptoKings … $18,000+/mo").
 *  They are gone rather than adapted: Clips Studio has no success stories to
 *  report yet, and made-up ones on a page selling a tool to people who make
 *  their living from clips would be both dishonest and obvious.
 *
 *  No auth on this surface — it is browsed before installing.
 */

import { Pitch } from "@/components/Pitch";

export const metadata = {
	title: "Clips Studio — free AI clipping that runs on your own PC",
	description:
		"Turn Twitch, Kick and YouTube VODs into vertical clips with captions, on your members' own machines. Free, open source, no credits and no watermark.",
};

export default function DiscoverPage() {
	return (
		<Pitch note="Add Clips Studio to your community and your clippers get it in their sidebar. Nothing to host and nothing to pay for — each member installs it on their own PC." />
	);
}
