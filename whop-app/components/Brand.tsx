/** Shared branded pieces, so the member, owner and discover views cannot
 *  drift apart on the facts or the look. */

import Image from "next/image";
import { LIMITS, LINKS, REQUIREMENTS, STEPS } from "@/lib/content";

export function Hero({
	eyebrow,
	title,
	children,
}: {
	eyebrow: string;
	title: string;
	children?: React.ReactNode;
}) {
	return (
		<header>
			<div className="cs-hero">
				<Image
					src="/mascot.png"
					alt="Clippy the Kitty, the app's mascot"
					width={84}
					height={84}
					priority
				/>
				<div>
					<p className="cs-eyebrow">{eyebrow}</p>
					<h1 className="cs-title">{title}</h1>
				</div>
			</div>
			{children}
		</header>
	);
}

export function Requirements() {
	return (
		<div className="cs-card">
			<dl style={{ margin: 0 }}>
				{REQUIREMENTS.map((r) => (
					<div className="cs-req" key={r.label}>
						<dt>{r.label}</dt>
						<dd>
							{r.value}
							{r.note ? <small>{r.note}</small> : null}
						</dd>
					</div>
				))}
			</dl>
		</div>
	);
}

export function Steps() {
	return (
		<ol className="cs-steps">
			{STEPS.map((s) => (
				<li key={s}>{s}</li>
			))}
		</ol>
	);
}

export function Limits() {
	return (
		<ul className="cs-limits">
			{LIMITS.map((l) => (
				<li key={l.lead}>
					<strong>{l.lead}</strong> {l.body}
				</li>
			))}
		</ul>
	);
}

export function DownloadButton({ label = "Download for Windows" }: { label?: string }) {
	return (
		<a className="cs-cta" href={LINKS.download} target="_blank" rel="noreferrer">
			{label}
			<span aria-hidden>&rarr;</span>
		</a>
	);
}

export function Footer() {
	return (
		<footer className="cs-foot">
			<a href={LINKS.site} target="_blank" rel="noreferrer">
				Website
			</a>
			{" · "}
			<a href={LINKS.github} target="_blank" rel="noreferrer">
				Source on GitHub
			</a>
			{" · "}
			<a href={LINKS.issues} target="_blank" rel="noreferrer">
				Report a problem
			</a>
			{" · "}
			<a href={LINKS.privacy} target="_blank" rel="noreferrer">
				Privacy
			</a>
			<p style={{ margin: "10px 0 0" }}>
				Clips Kitty is free and open source under AGPL-3.0, and is not
				affiliated with Whop. Everything runs on your own computer.
			</p>
		</footer>
	);
}
