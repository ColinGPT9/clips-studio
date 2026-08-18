"use client";

/** The paste-ready announcement for a community owner.
 *
 *  This is the piece that turns one owner into a few thousand members, so
 *  it is one click rather than a select-and-copy. Falls back to showing the
 *  text plainly if the clipboard API is unavailable, which it is in some
 *  embedded contexts.
 */

import { useState } from "react";
import { OWNER_BLURB } from "@/lib/content";

export function CopyBlurb() {
	const [copied, setCopied] = useState(false);

	async function copy() {
		try {
			await navigator.clipboard.writeText(OWNER_BLURB);
			setCopied(true);
			setTimeout(() => setCopied(false), 2200);
		} catch {
			// Clipboard blocked in this iframe — the text is on screen anyway.
			setCopied(false);
		}
	}

	return (
		<div className="cs-card">
			<pre className="cs-blurb">{OWNER_BLURB}</pre>
			<div className="cs-cta-row" style={{ marginTop: 14 }}>
				<button type="button" className="cs-ghost" onClick={copy}>
					{copied ? "Copied" : "Copy this message"}
				</button>
			</div>
		</div>
	);
}
