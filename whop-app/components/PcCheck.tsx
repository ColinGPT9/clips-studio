"use client";

/** An advisory "will this run on my PC?" hint.
 *
 *  Strictly a hint. Both signals it uses are unreliable — navigator.deviceMemory
 *  is capped at 8 and rounded to a power of two (so a 32 GB machine and a
 *  16 GB machine both report "8"), and the WebGL renderer string is masked
 *  in some browsers. So this NEVER blocks the download and never claims a
 *  machine is unsuitable; the worst it says is "we could not tell, here is
 *  what to check yourself".
 *
 *  It exists because the alternative is worse: someone on 8 GB downloads
 *  5 GB, processes a whole video, and gets nothing at the render stage.
 */

import { useEffect, useState } from "react";

type Verdict = {
	tone: "good" | "warn" | "unknown";
	headline: string;
	detail: string;
};

function detect(): Verdict {
	if (typeof navigator === "undefined") {
		return { tone: "unknown", headline: "", detail: "" };
	}

	const ua = navigator.userAgent;
	const isWindows = /Windows NT/i.test(ua);
	// navigator.deviceMemory is Chromium-only and capped at 8.
	const mem = (navigator as Navigator & { deviceMemory?: number }).deviceMemory;

	let gpu = "";
	try {
		const canvas = document.createElement("canvas");
		const gl = (canvas.getContext("webgl") ||
			canvas.getContext("experimental-webgl")) as WebGLRenderingContext | null;
		const ext = gl?.getExtension("WEBGL_debug_renderer_info");
		if (gl && ext) {
			gpu = String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) ?? "");
		}
	} catch {
		// Blocked or unsupported. Not worth reporting — it is a hint either way.
	}

	const nvidia = /nvidia|geforce|rtx|gtx/i.test(gpu);

	if (!isWindows) {
		return {
			tone: "warn",
			headline: "This browser does not look like Windows",
			detail:
				"Clips Studio is a Windows application and there is no Mac build yet. If you are reading this on a phone or a Mac, open it on your Windows PC.",
		};
	}

	// The 8 GB trap is the one worth calling out, and deviceMemory reports
	// exactly 8 for anything 8 GB or more, so it can only ever be a prompt
	// to check rather than a verdict.
	if (typeof mem === "number" && mem < 8) {
		return {
			tone: "warn",
			headline: "Your PC may not have enough memory",
			detail: `Your browser reports about ${mem} GB of RAM. Clips Studio needs 16 GB — below that it analyses the whole video and then fails at the render stage.`,
		};
	}

	if (nvidia) {
		return {
			tone: "good",
			headline: "Windows and an NVIDIA GPU detected",
			detail: `Looks like a good fit${gpu ? ` (${gpu})` : ""}. Check you have 16 GB of RAM and you are ready to go.`,
		};
	}

	return {
		tone: "unknown",
		headline: "Windows detected",
		detail:
			"Your browser will not tell a page how much RAM or which graphics card you have. Check the two that matter yourself: 16 GB of RAM, and ideally an NVIDIA graphics card.",
	};
}

export function PcCheck() {
	const [verdict, setVerdict] = useState<Verdict | null>(null);

	// Runs after mount so the server and client render the same markup.
	useEffect(() => {
		setVerdict(detect());
	}, []);

	if (!verdict || !verdict.headline) return null;

	const colour =
		verdict.tone === "good"
			? "var(--cs-success)"
			: verdict.tone === "warn"
				? "var(--cs-warn)"
				: "var(--cs-muted)";

	return (
		<div className="cs-card" style={{ borderColor: colour, marginTop: 12 }}>
			<p style={{ margin: 0, fontWeight: 600, fontSize: 14.5, color: colour }}>
				{verdict.headline}
			</p>
			<p className="cs-note" style={{ marginTop: 6 }}>
				{verdict.detail}
			</p>
		</div>
	);
}
