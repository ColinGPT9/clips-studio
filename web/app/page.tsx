"use client";

/** The whole tool: sign in, point it at a recording, get clips.
 *
 *  Everything here runs in the visitor's tab. There is no server component
 *  and no API route in this app on purpose — a key that is never sent
 *  anywhere cannot leak from anywhere, and that claim is only worth making if
 *  the architecture makes it structurally true rather than carefully
 *  observed. The same goes for the VOD: Twitch's and Kick's CDNs talk to the
 *  visitor's browser directly, so their footage never touches us either.
 *
 *  The flow is deliberately linear (choose a source → transcribe → score →
 *  cut) rather than a queue or a dashboard. This is a free taster used once
 *  by someone deciding whether to download the real thing; anything that
 *  needs managing belongs in the desktop app.
 */

import { useCallback, useEffect, useId, useRef, useState } from "react";
import {
	AudioDecodeError,
	AudioTooLongError,
	decodeToMono16k,
	MAX_DURATION_SECONDS,
	toChunks,
} from "@/lib/audio";
import { audioExcitement } from "@/lib/audioEvents";
import {
	clipFilename,
	cutClip,
	cutFromBlob,
	loadFFmpeg,
	mountSource,
	tsAudioToWav,
} from "@/lib/clip";
import {
	COST_NOTE,
	DESKTOP_IS_FREE,
	DESKTOP_ONLY,
	LINKS,
	PREFERRED_SCORE_MODEL,
	PREFERRED_TRANSCRIBE_MODEL,
	PRIVACY_NOTE,
} from "@/lib/content";
import {
	fetchAudio,
	fetchRange,
	loadFromMaster,
	offsetWithinRange,
	type VodInfo,
} from "@/lib/hls";
import {
	beginSignIn,
	clearKey,
	type KeyInfo,
	keyInfo,
	listModels,
	loadKey,
	type Model,
	type Segment,
	transcribeChunk,
} from "@/lib/openrouter";
import { type Clip, findClips } from "@/lib/score";
import { identify, masterPlaylistUrl } from "@/lib/source";

type Phase =
	| "idle"
	| "downloading"
	| "reading"
	| "listening"
	| "transcribing"
	| "scoring"
	| "done";

const PHASE_LABEL: Record<Phase, string> = {
	idle: "",
	downloading: "Downloading the audio",
	reading: "Reading the audio",
	listening: "Listening for reactions",
	transcribing: "Transcribing",
	scoring: "Finding the best moments",
	done: "",
};

type Mode = "file" | "url";

export default function Page() {
	const [apiKey, setApiKey] = useState<string | null>(null);
	const [account, setAccount] = useState<KeyInfo | null>(null);
	const [models, setModels] = useState<Model[]>([]);
	const [scoreModel, setScoreModel] = useState(PREFERRED_SCORE_MODEL);
	const [transcribeModel, setTranscribeModel] = useState(
		PREFERRED_TRANSCRIBE_MODEL,
	);

	const [mode, setMode] = useState<Mode>("file");
	const [file, setFile] = useState<File | null>(null);
	const [url, setUrl] = useState("");
	/** Held after a VOD run so cutting can fetch just the segments it needs,
	 *  instead of downloading the whole thing again per clip. */
	const [vod, setVod] = useState<VodInfo | null>(null);

	const [phase, setPhase] = useState<Phase>("idle");
	const [progress, setProgress] = useState({ done: 0, total: 0 });
	const [clips, setClips] = useState<Clip[] | null>(null);
	const [error, setError] = useState<string | null>(null);

	/** Object URLs for cut clips, by clip index. Revoked on unmount — a
	 *  handful of 50 MB blobs left dangling is real memory on the machines
	 *  this page is aimed at. */
	const [cuts, setCuts] = useState<Record<number, string>>({});
	const [cutting, setCutting] = useState<number | null>(null);
	const cutsRef = useRef(cuts);
	cutsRef.current = cuts;

	useEffect(() => {
		return () => {
			for (const u of Object.values(cutsRef.current)) URL.revokeObjectURL(u);
		};
	}, []);

	// ---- session ---------------------------------------------------------

	useEffect(() => {
		const stored = loadKey();
		if (!stored) return;
		setApiKey(stored);

		// Both are advisory: a failure here means the picker falls back to
		// defaults, not that the tool stops working.
		keyInfo(stored)
			.then(setAccount)
			.catch(() => setAccount(null));
		listModels(stored)
			.then(setModels)
			.catch(() => setModels([]));
	}, []);

	const signOut = () => {
		clearKey();
		setApiKey(null);
		setAccount(null);
		setModels([]);
		setClips(null);
	};

	// ---- the run ---------------------------------------------------------

	/** Get 16 kHz mono PCM from whichever source the visitor chose. */
	const readAudio = useCallback(async (): Promise<Float32Array> => {
		if (mode === "file") {
			if (!file) throw new Error("Choose a file first.");
			setPhase("reading");
			return decodeToMono16k(file);
		}

		const source = identify(url);
		if (source.kind === "unsupported") throw new Error(source.message);

		setPhase("downloading");
		setProgress({ done: 0, total: 1 });

		const info = await loadFromMaster(await masterPlaylistUrl(source));
		if (info.durationSeconds > MAX_DURATION_SECONDS) {
			throw new AudioTooLongError(
				`That VOD is ${Math.round(info.durationSeconds / 60)} minutes. This page handles up to ${
					MAX_DURATION_SECONDS / 60
				} — the desktop app sits on a three-hour VOD happily.`,
			);
		}
		setVod(info);

		const ts = await fetchAudio(info, (done, total) =>
			setProgress({ done, total }),
		);

		// Browsers decode MP4, WebM, MP3 and WAV but not raw MPEG-TS, so the
		// joined segments go through ffmpeg before the normal decode path.
		setPhase("reading");
		const ffmpeg = await loadFFmpeg();
		return decodeToMono16k(await tsAudioToWav(ffmpeg, ts));
	}, [mode, file, url]);

	const run = useCallback(async () => {
		if (!apiKey) return;

		setError(null);
		setClips(null);
		setCuts({});
		setVod(null);

		try {
			const pcm = await readAudio();

			// Cheap and worth it: the transcript alone misses the moment the
			// chat loses it. Pure signal processing over PCM we already hold,
			// so it costs no requests and no download. See audioEvents.ts.
			setPhase("listening");
			const excitement = audioExcitement(pcm);

			const chunks = toChunks(pcm);
			if (!chunks.length) {
				throw new Error("There is no audio long enough to transcribe.");
			}

			setPhase("transcribing");
			setProgress({ done: 0, total: chunks.length });

			// Sequential, not parallel: free OpenRouter accounts are capped at
			// 20 requests a minute, and firing every chunk at once turns a
			// working run into a wall of 429s.
			const segments: Segment[] = [];
			for (let i = 0; i < chunks.length; i++) {
				segments.push(
					...(await transcribeChunk(
						apiKey,
						chunks[i].blob,
						transcribeModel,
						chunks[i].offsetSeconds,
					)),
				);
				setProgress({ done: i + 1, total: chunks.length });
			}

			if (!segments.length) {
				throw new Error(
					"Nothing was transcribed. If the recording has no speech there is nothing to score.",
				);
			}

			setPhase("scoring");
			setProgress({ done: 0, total: 1 });

			const found = await findClips(
				apiKey,
				scoreModel,
				segments,
				excitement,
				(done, total) => setProgress({ done, total }),
			);

			setClips(found);
			setPhase("done");
		} catch (e) {
			setError(
				e instanceof AudioTooLongError || e instanceof AudioDecodeError
					? e.message
					: e instanceof Error
						? e.message
						: "Something went wrong.",
			);
			setPhase("idle");
		}
	}, [apiKey, readAudio, scoreModel, transcribeModel]);

	const cut = useCallback(
		async (clip: Clip, index: number) => {
			setCutting(index);
			setError(null);
			try {
				const ffmpeg = await loadFFmpeg();
				const name = clipFilename(index, clip.hook);

				const result = vod
					? await cutFromBlob(
							ffmpeg,
							// Only the segments covering this moment, so cutting one
							// clip out of a two-hour VOD downloads seconds of video
							// rather than gigabytes.
							await fetchRange(vod, clip.start, clip.end),
							offsetWithinRange(vod, clip.start),
							clip.end - clip.start,
							name,
						)
					: file
						? await cutClip(
								ffmpeg,
								await mountSource(ffmpeg, file),
								clip.start,
								clip.end,
								name,
							)
						: null;

				if (!result) throw new Error("The source is no longer available.");
				setCuts((prev) => ({
					...prev,
					[index]: URL.createObjectURL(result.blob),
				}));
			} catch (e) {
				setError(e instanceof Error ? e.message : "Could not cut that clip.");
			} finally {
				setCutting(null);
			}
		},
		[file, vod],
	);

	const busy = phase !== "idle" && phase !== "done";
	const ready = mode === "file" ? Boolean(file) : url.trim().length > 0;

	// Only models that can actually return JSON on demand. A model without
	// `response_format` is not a worse choice for scoring, it is a broken one.
	const scoreChoices = models
		.filter((m) => m.supportsJson && !m.isTranscription)
		.sort((a, b) => (a.promptPerM ?? 1e9) - (b.promptPerM ?? 1e9));
	const transcribeChoices = models.filter((m) => m.isTranscription);

	return (
		<main className="mx-auto max-w-3xl px-5 py-10 sm:py-16">
			<Header signedIn={Boolean(apiKey)} onSignOut={signOut} />

			{!apiKey ? (
				<SignIn />
			) : (
				<>
					<Controls
						account={account}
						mode={mode}
						onMode={setMode}
						onFile={setFile}
						url={url}
						onUrl={setUrl}
						busy={busy}
						ready={ready}
						scoreModel={scoreModel}
						onScoreModel={setScoreModel}
						scoreChoices={scoreChoices}
						transcribeModel={transcribeModel}
						onTranscribeModel={setTranscribeModel}
						transcribeChoices={transcribeChoices}
						onRun={run}
					/>

					{busy && (
						<Progress
							label={PHASE_LABEL[phase]}
							done={progress.done}
							total={progress.total}
						/>
					)}
				</>
			)}

			{error && (
				<p
					className="cs-card mt-6 p-4 text-sm leading-relaxed"
					style={{ color: "var(--cs-danger)" }}
				>
					{error}
				</p>
			)}

			{clips && (
				<Results
					clips={clips}
					cuts={cuts}
					cutting={cutting}
					onCut={cut}
					canCut={Boolean(file || vod)}
				/>
			)}

			<DesktopPitch />
		</main>
	);
}

// ---- pieces --------------------------------------------------------------

function Header({
	signedIn,
	onSignOut,
}: {
	signedIn: boolean;
	onSignOut: () => void;
}) {
	return (
		<header className="mb-8 flex items-start justify-between gap-4">
			<div className="flex items-center gap-3">
				{/* biome-ignore lint/performance/noImgElement: next/image cannot
				    optimise anything in a static export — it requires
				    `images.unoptimized`, which makes it a plain <img> wearing a
				    costume. The rule's real concern is weight, and that is
				    handled instead: this file is pre-scaled to 128px and 17 KB,
				    down from the 166 KB original in docs/brand. */}
				<img
					src="/mascot-head.png"
					alt=""
					width={52}
					height={52}
					className="h-11 w-11 shrink-0 sm:h-13 sm:w-13"
				/>
				<div>
					<h1 className="text-2xl font-bold sm:text-3xl">Clips Kitty Web</h1>
					<p className="mt-1 text-sm" style={{ color: "var(--cs-muted)" }}>
						Find the moments worth clipping, in your browser.
					</p>
				</div>
			</div>
			{signedIn && (
				<button
					type="button"
					onClick={onSignOut}
					className="cs-btn-quiet shrink-0 px-3 py-1.5 text-sm"
				>
					Sign out
				</button>
			)}
		</header>
	);
}

function SignIn() {
	const [pending, setPending] = useState(false);

	return (
		<section className="cs-card p-6">
			<h2 className="text-lg font-semibold">
				Sign in with OpenRouter to start
			</h2>
			<p className="mt-3 text-sm leading-relaxed">{PRIVACY_NOTE}</p>
			<p className="mt-3 text-sm leading-relaxed">{COST_NOTE}</p>

			<button
				type="button"
				disabled={pending}
				onClick={() => {
					setPending(true);
					beginSignIn(`${window.location.origin}/callback`);
				}}
				className="cs-btn mt-5 px-5 py-2.5"
			>
				{pending ? "Opening OpenRouter…" : "Sign in with OpenRouter"}
			</button>

			<p className="mt-4 text-xs" style={{ color: "var(--cs-muted)" }}>
				You will be asked to authorise Clips Kitty Web on your own OpenRouter
				account. We never see your key — it is stored in this browser only.
			</p>

			{/* Said here, before they spend anything, rather than only at the
			    bottom of the page. Someone on Windows may simply want the free
			    app instead, and burying that until after they have paid for a
			    run would be the dishonest way to sell it. */}
			<p className="cs-raised mt-5 p-3 text-sm leading-relaxed">
				On Windows?{" "}
				<a
					href={LINKS.download}
					className="underline"
					style={{ color: "var(--cs-accent)" }}
				>
					The desktop app is free
				</a>{" "}
				and needs no account or credits at all — it runs the AI on your own PC,
				and it does the vertical cropping and captions this page cannot.
			</p>
		</section>
	);
}

function Controls({
	account,
	mode,
	onMode,
	onFile,
	url,
	onUrl,
	busy,
	ready,
	scoreModel,
	onScoreModel,
	scoreChoices,
	transcribeModel,
	onTranscribeModel,
	transcribeChoices,
	onRun,
}: {
	account: KeyInfo | null;
	mode: Mode;
	onMode: (m: Mode) => void;
	onFile: (f: File | null) => void;
	url: string;
	onUrl: (u: string) => void;
	busy: boolean;
	ready: boolean;
	scoreModel: string;
	onScoreModel: (id: string) => void;
	scoreChoices: Model[];
	transcribeModel: string;
	onTranscribeModel: (id: string) => void;
	transcribeChoices: Model[];
	onRun: () => void;
}) {
	const fileId = useId();
	const urlId = useId();

	// Shown as the visitor types, so a YouTube link explains itself before
	// they press the button and wait for a failure.
	const hint = mode === "url" && url.trim() ? identify(url) : null;

	return (
		<section className="cs-card p-6">
			{account?.isFreeTier && (
				<p
					className="cs-raised mb-5 p-3 text-sm leading-relaxed"
					style={{ color: "var(--cs-warn)" }}
				>
					Your OpenRouter account is on the free tier: 50 requests a day. A
					recording of about 10 minutes fits comfortably; an hour will not.
					Buying $10 of credit once raises it to 1000 a day.
				</p>
			)}

			<div className="mb-4 flex gap-2">
				<button
					type="button"
					disabled={busy}
					onClick={() => onMode("file")}
					className={
						mode === "file"
							? "cs-btn px-4 py-1.5 text-sm"
							: "cs-btn-quiet px-4 py-1.5 text-sm"
					}
				>
					A file
				</button>
				<button
					type="button"
					disabled={busy}
					onClick={() => onMode("url")}
					className={
						mode === "url"
							? "cs-btn px-4 py-1.5 text-sm"
							: "cs-btn-quiet px-4 py-1.5 text-sm"
					}
				>
					A Twitch or Kick VOD
				</button>
			</div>

			{mode === "file" ? (
				<>
					<label className="block text-sm font-semibold" htmlFor={fileId}>
						Your recording
					</label>
					<input
						id={fileId}
						type="file"
						accept="video/*,audio/*"
						disabled={busy}
						onChange={(e) => onFile(e.target.files?.[0] ?? null)}
						className="mt-2 block w-full text-sm"
					/>
					<p className="mt-2 text-xs" style={{ color: "var(--cs-muted)" }}>
						MP4, MOV, WebM, MP3 or WAV, up to {MAX_DURATION_SECONDS / 60}{" "}
						minutes. It stays on your computer — nothing is uploaded.
					</p>
				</>
			) : (
				<>
					<label className="block text-sm font-semibold" htmlFor={urlId}>
						Twitch or Kick VOD link
					</label>
					<input
						id={urlId}
						type="url"
						placeholder="https://www.twitch.tv/videos/123456789"
						value={url}
						disabled={busy}
						onChange={(e) => onUrl(e.target.value)}
						className="cs-raised mt-2 w-full px-3 py-2 text-sm"
					/>
					<p className="mt-2 text-xs" style={{ color: "var(--cs-muted)" }}>
						Downloads only the cheapest audio track to find moments, then just
						the seconds it needs to cut them. Straight from Twitch or Kick to
						your browser.
					</p>
					{hint?.kind === "unsupported" && (
						<p
							className="cs-raised mt-3 p-3 text-sm leading-relaxed"
							style={{ color: "var(--cs-warn)" }}
						>
							{hint.message}
						</p>
					)}
				</>
			)}

			<div className="mt-5 grid gap-4 sm:grid-cols-2">
				<ModelPicker
					label="Transcription"
					value={transcribeModel}
					onChange={onTranscribeModel}
					choices={transcribeChoices}
					disabled={busy}
				/>
				<ModelPicker
					label="Scoring"
					value={scoreModel}
					onChange={onScoreModel}
					choices={scoreChoices}
					disabled={busy}
				/>
			</div>

			<button
				type="button"
				disabled={!ready || busy}
				onClick={onRun}
				className="cs-btn mt-6 px-5 py-2.5"
			>
				Find the best moments
			</button>
		</section>
	);
}

function ModelPicker({
	label,
	value,
	onChange,
	choices,
	disabled,
}: {
	label: string;
	value: string;
	onChange: (id: string) => void;
	choices: Model[];
	disabled: boolean;
}) {
	// Until the list loads — or if it fails — the recommended default is still
	// a valid model id, so the run works with the picker showing one option.
	const options = choices.length
		? choices
		: [
				{
					id: value,
					name: value,
					contextLength: 0,
					promptPerM: null,
					completionPerM: null,
					supportsJson: true,
					isTranscription: false,
				},
			];

	return (
		<div>
			<label className="block text-sm font-semibold" htmlFor={`model-${label}`}>
				{label}
			</label>
			<select
				id={`model-${label}`}
				value={value}
				disabled={disabled}
				onChange={(e) => onChange(e.target.value)}
				className="cs-raised mt-2 w-full px-3 py-2 text-sm"
			>
				{options.map((m) => (
					<option key={m.id} value={m.id}>
						{m.name}
						{m.promptPerM !== null && ` — $${m.promptPerM.toFixed(2)}/M in`}
					</option>
				))}
			</select>
		</div>
	);
}

function Progress({
	label,
	done,
	total,
}: {
	label: string;
	done: number;
	total: number;
}) {
	const pct = total ? Math.round((done / total) * 100) : 0;
	return (
		<section className="cs-card mt-6 p-5">
			<div className="flex items-baseline justify-between text-sm">
				<span className="font-semibold">{label}</span>
				{total > 1 && (
					<span style={{ color: "var(--cs-muted)" }}>
						{done} of {total}
					</span>
				)}
			</div>
			<div
				className="mt-3 h-2 w-full overflow-hidden rounded-full"
				style={{ background: "var(--cs-raised)" }}
			>
				<div
					className="h-full rounded-full transition-all"
					style={{ width: `${pct}%`, background: "var(--cs-accent)" }}
				/>
			</div>
		</section>
	);
}

function timecode(seconds: number): string {
	const m = Math.floor(seconds / 60);
	const s = Math.floor(seconds % 60);
	return `${m}:${String(s).padStart(2, "0")}`;
}

function Results({
	clips,
	cuts,
	cutting,
	onCut,
	canCut,
}: {
	clips: Clip[];
	cuts: Record<number, string>;
	cutting: number | null;
	onCut: (clip: Clip, index: number) => void;
	canCut: boolean;
}) {
	if (!clips.length) {
		return (
			<section className="cs-card mt-6 p-6">
				<h2 className="text-lg font-semibold">Nothing scored highly enough</h2>
				<p className="mt-2 text-sm leading-relaxed">
					The model did not find a self-contained moment it rated worth posting.
					That is a real answer, not a failure — try a longer recording, or one
					with more talking in it.
				</p>
			</section>
		);
	}

	return (
		<section className="mt-8">
			<h2 className="text-lg font-semibold">
				{clips.length} {clips.length === 1 ? "moment" : "moments"} worth
				clipping
			</h2>

			<ul className="mt-4 space-y-3">
				{clips.map((clip, i) => (
					<li key={`${clip.start}-${clip.end}`} className="cs-card p-5">
						<div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
							<span
								className="font-mono text-sm"
								style={{ color: "var(--cs-accent)" }}
							>
								{timecode(clip.start)}–{timecode(clip.end)}
							</span>
							<span className="text-xs" style={{ color: "var(--cs-muted)" }}>
								score {clip.score}
								{clip.engagement !== null && ` · hook ${clip.engagement}`}
							</span>
							{clip.trending && (
								<span
									className="rounded px-1.5 py-0.5 text-xs"
									style={{
										background: "var(--cs-raised)",
										color: "var(--cs-warn)",
									}}
								>
									trending
								</span>
							)}
						</div>

						<p className="mt-2 font-semibold">{clip.hook}</p>
						{clip.reason && (
							<p
								className="mt-1 text-sm leading-relaxed"
								style={{ color: "var(--cs-muted)" }}
							>
								{clip.reason}
							</p>
						)}

						<div className="mt-4">
							{cuts[i] ? (
								<a
									href={cuts[i]}
									download={clipFilename(i, clip.hook)}
									className="cs-btn inline-block px-4 py-2 text-sm"
								>
									Download clip
								</a>
							) : (
								<button
									type="button"
									disabled={!canCut || cutting !== null}
									onClick={() => onCut(clip, i)}
									className="cs-btn-quiet px-4 py-2 text-sm"
								>
									{cutting === i ? "Cutting…" : "Cut this clip"}
								</button>
							)}
						</div>
					</li>
				))}
			</ul>

			<p className="mt-4 text-xs" style={{ color: "var(--cs-muted)" }}>
				Clips are cut without re-encoding, so they are quick but start at the
				nearest keyframe — up to a second or so early. They come out at the
				original shape, ready to edit.
			</p>
		</section>
	);
}

function DesktopPitch() {
	return (
		<section className="mt-14">
			<h2 className="text-lg font-semibold">
				The Windows app is free — and does much more
			</h2>
			<p className="mt-2 text-sm leading-relaxed">{DESKTOP_IS_FREE}</p>
			<p
				className="mt-2 text-sm leading-relaxed"
				style={{ color: "var(--cs-muted)" }}
			>
				This page runs in a browser, so it stops well short of what the real
				thing does.
			</p>

			<ul className="mt-5 grid gap-4 sm:grid-cols-2">
				{DESKTOP_ONLY.map((item) => (
					<li key={item.title} className="cs-card p-4">
						<p className="text-sm font-semibold">{item.title}</p>
						<p
							className="mt-1 text-sm leading-relaxed"
							style={{ color: "var(--cs-muted)" }}
						>
							{item.body}
						</p>
					</li>
				))}
			</ul>

			<div className="mt-6 flex flex-wrap gap-3">
				<a href={LINKS.download} className="cs-btn px-5 py-2.5 text-sm">
					Download Clips Kitty — free
				</a>
				<a href={LINKS.github} className="cs-btn-quiet px-5 py-2.5 text-sm">
					Source on GitHub
				</a>
			</div>

			<p className="mt-4 text-xs" style={{ color: "var(--cs-muted)" }}>
				Needs Windows and 16 GB of RAM. An NVIDIA graphics card makes it much
				faster, but it runs without one.
			</p>
		</section>
	);
}
