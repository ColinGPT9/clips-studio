"use client";

/** Where OpenRouter sends the visitor back after they authorise us.
 *
 *  Reads `?code=` straight off `window.location` rather than through
 *  `useSearchParams`. That hook forces the page into a Suspense boundary
 *  under `output: export`, which is a lot of ceremony for one query
 *  parameter on a page that exists for a quarter of a second.
 *
 *  The exchange happens here, in the browser, on the visitor's behalf. There
 *  is no route handler behind this — if there were, the key would pass
 *  through our server, which is the one thing this app promises never
 *  happens.
 */

import { useEffect, useState } from "react";
import { completeSignIn } from "@/lib/openrouter";

export default function CallbackPage() {
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		const code = new URLSearchParams(window.location.search).get("code");

		if (!code) {
			setError("OpenRouter did not send a sign-in code back.");
			return;
		}

		completeSignIn(code)
			// replace(), not href: the code is single-use, so leaving this URL
			// in history means Back lands on a page that can only fail.
			.then(() => window.location.replace("/"))
			.catch((e: unknown) =>
				setError(e instanceof Error ? e.message : "Sign-in failed."),
			);
	}, []);

	return (
		<main className="mx-auto max-w-md px-5 py-20 text-center">
			{error ? (
				<>
					<h1 className="text-lg font-semibold">Sign-in did not complete</h1>
					<p className="mt-3 text-sm leading-relaxed">{error}</p>
					<a href="/" className="cs-btn mt-6 inline-block px-5 py-2.5 text-sm">
						Back to the start
					</a>
				</>
			) : (
				<p className="text-sm" style={{ color: "var(--cs-muted)" }}>
					Finishing sign-in…
				</p>
			)}
		</main>
	);
}
