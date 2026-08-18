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
			</body>
		</html>
	);
}
