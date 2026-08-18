import { Analytics } from "@vercel/analytics/next";
import { WhopApp } from "@whop/react/components";
import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
	title: "Clips Studio",
	description:
		"Free, open-source AI video clipping that runs on your own PC. Turn Twitch, Kick and YouTube VODs into vertical clips with captions.",
	icons: { icon: "/mascot-head.png" },
};

export default function RootLayout({
	children,
}: Readonly<{
	children: React.ReactNode;
}>) {
	return (
		// suppressHydrationWarning: Whop's theme script sets attributes on
		// <html> before React hydrates.
		<html lang="en" suppressHydrationWarning>
			<body className="antialiased">
				<WhopApp>{children}</WhopApp>
				{/* Vercel Web Analytics: page views only, no cookies and no
				    cross-site tracking, so it needs no consent banner. It
				    measures THIS marketing page — the desktop app still has no
				    telemetry, and nothing here can see what anyone clips.
				    Sends nothing until Web Analytics is switched on for the
				    project in the Vercel dashboard. */}
				<Analytics />
			</body>
		</html>
	);
}
