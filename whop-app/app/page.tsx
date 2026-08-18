/** The deployment root.
 *
 *  Reached by anyone who opens the URL directly rather than through a Whop
 *  community — someone you sent the link to, or a reviewer. So it is the
 *  full pitch, not an explanation of what a Whop app is.
 */

import { Pitch } from "@/components/Pitch";

export const metadata = {
	title: "Clips Studio — free AI clipping that runs on your own PC",
	description:
		"Turn Twitch, Kick and YouTube VODs into vertical clips with word-synced captions, on your own machine. Free, open source, no credits and no watermark.",
};

export default function Page() {
	return <Pitch />;
}
