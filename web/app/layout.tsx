import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
	title: "Clips Kitty Web — find your best moments in the browser",
	description:
		"Drop in a recording and get the moments worth clipping, cut and ready to edit. Runs entirely in your browser on your own OpenRouter account. No upload, no account with us.",
};

export default function RootLayout({
	children,
}: Readonly<{ children: React.ReactNode }>) {
	return (
		<html lang="en">
			<body className="antialiased">{children}</body>
		</html>
	);
}
