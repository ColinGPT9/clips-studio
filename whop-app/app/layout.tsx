import { Analytics } from "@vercel/analytics/next";
import { WhopApp } from "@whop/react/components";
import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
	title: "Clips Kitty",
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
				{/* appearance defaults to "inherit", and Whop's inline theme
				    script reads a cookie (falling back to the viewer's OS
				    setting) and puts a light/dark class on <html>. So without
				    this prop, half the page's identity is decided by whichever
				    theme the person browsing Whop happens to use.

				    That is not a hypothetical mismatch: this page's body is
				    hard-coded to the brand's dark palette in globals.css. A
				    frosted-ui control rendering in light appearance sits on a
				    dark background with light-mode colours — the same shape of
				    problem as the white-on-white text this app has already had
				    once, and invisible to anyone whose own Whop is dark.

				    Pinning it also answers Whop's "polished in both light and
				    dark mode" review criterion the honest way: the page is
				    deliberately dark for everyone, matching the product it
				    advertises, rather than half-adopting a theme it has no
				    colours for.

				    accentColor sky matches --cs-accent (#38bdf8), so anything
				    frosted-ui draws agrees with the rest of the page. */}
				<WhopApp appearance="dark" accentColor="sky">
					{children}
				</WhopApp>
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
