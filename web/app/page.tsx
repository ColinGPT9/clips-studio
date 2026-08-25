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
import { probeDuration, SEGMENT_SECONDS, segmentAudio } from "@/lib/audio";
import {
	combineFeatures,
	extractFeatures,
	type SecondFeatures,
} from "@/lib/audioEvents";
import {
	clipFilename,
	cutClip,
	cutFromBlob,
	discardSource,
	loadFFmpeg,
	mountSource,
	recentLog,
	writeSource,
} from "@/lib/clip";
import {
	COST_NOTE,
	DESKTOP_IS_FREE,
	DESKTOP_ONLY,
	DONATE_NOTE,
	DONATE_TITLE,
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
	listTranscriptionModels,
	loadKey,
	type Model,
	type Segment,
	transcribeChunk,
} from "@/lib/openrouter";
import { type Clip, estimateScoringRequests, findClips } from "@/lib/score";
import { identify, masterPlaylistUrl, SUPPORTED_LABEL } from "@/lib/source";

type Phase =
	| "idle"
	| "downloading"
	| "reading"
	| "transcribing"
	| "scoring"
	| "done";

const PHASE_LABEL: Record<Phase, string> = {
	idle: "",
	downloading: "Downloading the audio",
	reading: "Splitting the audio up",
	transcribing: "Transcribing and listening for reactions",
	scoring: "Finding the best moments",
	done: "",
};

type Mode = "file" | "url";

/** Requests a recording of this length will cost at OpenRouter: one
 *  transcription call per audio segment, plus the scoring calls. */
function estimateRequests(durationSeconds: number): number {
	if (!durationSeconds) return 0;
	return (
		Math.ceil(durationSeconds / SEGMENT_SECONDS) +
		estimateScoringRequests(durationSeconds)
	);
}

/** Render whatever was thrown, whatever shape it is.
 *
 *  Not defensive programming for its own sake — ffmpeg.wasm genuinely rejects
 *  with STRINGS. `@ffmpeg/ffmpeg`'s worker posts `data: e.toString()`
 *  (worker.js:153) and the client rejects with that raw value
 *  (classes.js:54), so nothing it fails at is ever an `Error`.
 *
 *  The previous version tested `instanceof Error` and fell back to "Something
 *  went wrong" — which meant that for weeks the app reported every ffmpeg
 *  failure with those three words while the library was saying, precisely,
 *  "failed to import ffmpeg-core.js". The real message was there the whole
 *  time and this line threw it away. */
function describeError(e: unknown): string {
	if (e instanceof Error) return e.message;
	if (typeof e === "string") return e;
	return String(e);
}

/** What a free OpenRouter account gets per day. Buying $10 of credit once
 *  raises it to 1000, which is why the warning says so rather than just
 *  refusing. */
const FREE_DAILY_REQUESTS = 50;

export default function Page() {
	const [apiKey, setApiKey] = useState<string | null>(null);
	const [account, setAccount] = useState<KeyInfo | null>(null);
	const [models, setModels] = useState<Model[]>([]);
	/** Kept apart from `models` because they come from a different endpoint:
	 *  `/models/user` has no speech-to-text models in it, which is why the
	 *  transcription picker used to have nothing to offer. */
	const [sttModels, setSttModels] = useState<Model[]>([]);
	const [scoreModel, setScoreModel] = useState(PREFERRED_SCORE_MODEL);
	const [transcribeModel, setTranscribeModel] = useState(
		PREFERRED_TRANSCRIBE_MODEL,
	);

	const [mode, setMode] = useState<Mode>("file");
	const [file, setFile] = useState<File | null>(null);
	const [url, setUrl] = useState("");
	/** Length of the chosen source, once known — from a cheap metadata probe
	 *  for a file, or the manifest for a VOD. Drives the cost estimate shown
	 *  before anything is spent. */
	const [duration, setDuration] = useState(0);
	/** Held after a VOD run so cutting can fetch just the segments it needs,
	 *  instead of downloading the whole thing again per clip. */
	const [vod, setVod] = useState<VodInfo | null>(null);

	const [phase, setPhase] = useState<Phase>("idle");
	const [progress, setProgress] = useState({ done: 0, total: 0 });
	const [clips, setClips] = useState<Clip[] | null>(null);
	const [error, setError] = useState<string | null>(null);
	/** What the run has actually cost, as reported by OpenRouter. Exact, unlike
	 *  the estimate in the picker. */
	const [spent, setSpent] = useState(0);

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

	// Needs no key, so the picker is populated whether or not anyone is signed
	// in — and a failure here only costs the dropdown, not the run.
	useEffect(() => {
		listTranscriptionModels()
			.then(setSttModels)
			.catch(() => setSttModels([]));
	}, []);

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

	const chooseFile = useCallback((f: File | null) => {
		setFile(f);
		setDuration(0);
		// Reads only the container header, so this is instant even on a
		// multi-gigabyte recording — and it is what makes the cost estimate
		// available BEFORE the run rather than after it has spent anything.
		if (f)
			probeDuration(f)
				.then(setDuration)
				.catch(() => setDuration(0));
	}, []);

	const signOut = () => {
		clearKey();
		setApiKey(null);
		setAccount(null);
		setModels([]);
		setClips(null);
	};

	// ---- the run ---------------------------------------------------------

	/** Get the audio into ffmpeg's filesystem, and say how long it runs.
	 *
	 *  A local file is MOUNTED rather than copied — WORKERFS reads it lazily,
	 *  so a multi-gigabyte recording costs nothing to make available. A VOD has
	 *  no file behind it, so its joined segments are written instead; that is
	 *  the one real copy, and it is the cheap audio-only track. */
	const prepareSource = useCallback(async (): Promise<{
		ffmpeg: Awaited<ReturnType<typeof loadFFmpeg>>;
		inputPath: string;
		durationSeconds: number;
		written: string | null;
	}> => {
		const ffmpeg = await loadFFmpeg();

		if (mode === "file") {
			if (!file) throw new Error("Choose a file first.");
			setPhase("reading");
			return {
				ffmpeg,
				inputPath: `/mount/${await mountSource(ffmpeg, file)}`,
				durationSeconds: await probeDuration(file).catch(() => 0),
				written: null,
			};
		}

		const source = identify(url);
		if (source.kind === "unsupported") throw new Error(source.message);

		setPhase("downloading");
		setProgress({ done: 0, total: 1 });

		const info = await loadFromMaster(await masterPlaylistUrl(source));
		setVod(info);

		const ts = await fetchAudio(info, (done, total) =>
			setProgress({ done, total }),
		);

		setPhase("reading");
		return {
			ffmpeg,
			inputPath: await writeSource(ffmpeg, ts, "vod-audio.ts"),
			durationSeconds: info.durationSeconds,
			written: "vod-audio.ts",
		};
	}, [mode, file, url]);

	const run = useCallback(async () => {
		if (!apiKey) return;

		setError(null);
		setClips(null);
		setCuts({});
		setVod(null);
		setSpent(0);

		let ffmpeg: Awaited<ReturnType<typeof loadFFmpeg>> | null = null;
		let written: string | null = null;

		try {
			const prepared = await prepareSource();
			ffmpeg = prepared.ffmpeg;
			written = prepared.written;
			setDuration(prepared.durationSeconds);

			// Last moment before anything is spent. A VOD's length is only known
			// once its manifest is read, so this cannot happen any earlier for
			// that path — but it still happens before the first OpenRouter call,
			// which is the part that matters. Failing here costs nothing;
			// failing halfway through costs everything spent so far.
			const cost = estimateRequests(prepared.durationSeconds);
			if (account?.isFreeTier && cost > FREE_DAILY_REQUESTS) {
				throw new Error(
					`That recording is about ${Math.round(prepared.durationSeconds / 60)} minutes, which needs roughly ${cost} OpenRouter requests. A free account allows ${FREE_DAILY_REQUESTS} a day, so this would stop partway through. Buying $10 of credit once raises the limit to 1000 a day — or try a shorter recording.`,
				);
			}

			// Transcribe and measure one segment at a time, keeping only the
			// transcript lines and three numbers per second. This is what lets a
			// six-hour VOD cost the same memory as a ten-minute one — holding
			// the decoded recording would be 230 MB per hour.
			setPhase("transcribing");
			const expected = prepared.durationSeconds
				? Math.max(1, Math.ceil(prepared.durationSeconds / SEGMENT_SECONDS))
				: 0;
			setProgress({ done: 0, total: expected });

			const transcript: Segment[] = [];
			const features: SecondFeatures[] = [];
			let carry = 0;
			let seen = 0;

			// Sequential, not parallel: free OpenRouter accounts are capped at
			// 20 requests a minute, and firing every segment at once turns a
			// working run into a wall of 429s.
			for await (const segment of segmentAudio(ffmpeg, prepared.inputPath)) {
				const measured = extractFeatures(segment.pcm, carry);
				carry = measured.lastFrameRms;
				features.push(measured);

				transcript.push(
					...(await transcribeChunk(
						apiKey,
						segment.blob,
						transcribeModel,
						segment.offsetSeconds,
						(usd) => setSpent((total) => total + usd),
					)),
				);

				seen++;
				setProgress({ done: seen, total: Math.max(expected, seen) });
			}

			if (written && ffmpeg) {
				await discardSource(ffmpeg, written);
				written = null;
			}

			if (!transcript.length) {
				throw new Error(
					"Nothing was transcribed. If the recording has no speech there is nothing to score.",
				);
			}

			setPhase("scoring");
			setProgress({ done: 0, total: 1 });

			const found = await findClips(
				apiKey,
				scoreModel,
				transcript,
				combineFeatures(features),
				(done, total) => setProgress({ done, total }),
			);

			setClips(found);
			setPhase("done");
		} catch (e) {
			// ffmpeg says useful things ("Unknown format", "No such file or
			// directory") that only reach us through its log, so carry the tail
			// of it alongside the thrown value.
			const detail = recentLog();
			setError(
				detail
					? `${describeError(e)}

${detail}`
					: describeError(e),
			);
			setPhase("idle");
		} finally {
			// A failed run must not leave a 300 MB VOD sitting in ffmpeg's
			// filesystem for the rest of the session.
			if (written && ffmpeg) await discardSource(ffmpeg, written);
		}
	}, [apiKey, account, prepareSource, scoreModel, transcribeModel]);

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
				setError(describeError(e));
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
	// Sorted by the per-HOUR estimate, not the raw price. Sorting by the raw
	// number was wrong: OpenRouter reports speech pricing per second for some
	// models and per minute for others, so ranking them against each other put
	// a $0.36/hour model above a $0.012/hour one.
	const transcribeChoices = [...sttModels].sort(
		(a, b) => (a.audioPerHour ?? 1e9) - (b.audioPerHour ?? 1e9),
	);

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
						selectedFile={file}
						durationSeconds={duration}
						onFile={chooseFile}
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
							spent={spent}
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
						Find the moments worth clipping, in your browser.{" "}
						{/* Said up here, not only in the footer. A good share of the
						    people who arrive from OpenRouter's app directory are
						    developers rather than clippers, and for them "open
						    source, AGPL" is the interesting fact about this page —
						    they should not have to scroll past the whole pitch to
						    find the repo. */}
						<a
							href={LINKS.github}
							className="underline underline-offset-2"
							style={{ color: "var(--cs-accent)" }}
						>
							Open source
						</a>
						, AGPL-3.0.
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
	selectedFile,
	durationSeconds,
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
	selectedFile: File | null;
	durationSeconds: number;
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
					Your OpenRouter account is on the free tier: {FREE_DAILY_REQUESTS}{" "}
					requests a day, which covers roughly two hours of scoring. But
					transcription needs a funded account whatever the allowance says —
					OpenRouter wants at least $0.50 of balance before it will accept audio
					at all.
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
					A {SUPPORTED_LABEL()} VOD
				</button>
			</div>

			{mode === "file" ? (
				<>
					<span className="block text-sm font-semibold">Your recording</span>

					{/* The native file input is hidden and driven by this label.
					    Left visible it renders as the browser's own tiny grey
					    "Choose File" control, which on this dark page reads as
					    nothing at all — the first person to try it could not find
					    the upload button. A label IS the accessible control for a
					    file input, so this keeps keyboard and screen-reader
					    behaviour while looking like the rest of the page. */}
					<label
						htmlFor={fileId}
						className="cs-raised mt-2 flex cursor-pointer flex-col items-center gap-1 px-4 py-6 text-center transition-colors hover:border-current"
						style={{ borderStyle: "dashed" }}
					>
						<span
							className="text-sm font-semibold"
							style={{ color: "var(--cs-accent)" }}
						>
							{selectedFile ? "Choose a different file" : "Choose a video file"}
						</span>
						<span className="text-xs" style={{ color: "var(--cs-muted)" }}>
							{selectedFile
								? `${selectedFile.name} · ${(selectedFile.size / 1e6).toFixed(0)} MB`
								: "MP4, MOV, WebM, MP3 or WAV — any length"}
						</span>
					</label>
					<input
						id={fileId}
						type="file"
						accept="video/*,audio/*"
						disabled={busy}
						onChange={(e) => onFile(e.target.files?.[0] ?? null)}
						className="sr-only"
					/>
					<p className="mt-2 text-xs" style={{ color: "var(--cs-muted)" }}>
						It stays on your computer — the video is read in the browser and
						never uploaded to us.
					</p>
				</>
			) : (
				<>
					<label className="block text-sm font-semibold" htmlFor={urlId}>
						{SUPPORTED_LABEL()} VOD link
					</label>
					<input
						id={urlId}
						type="url"
						placeholder="https://www.twitch.tv/videos/… or kick.com/…"
						value={url}
						disabled={busy}
						onChange={(e) => onUrl(e.target.value)}
						className="cs-raised mt-2 w-full px-3 py-2 text-sm"
					/>
					<p className="mt-2 text-xs" style={{ color: "var(--cs-muted)" }}>
						Downloads only the cheapest audio track to find moments, then just
						the seconds it needs to cut them. YouTube links need the desktop
						app.
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

			{/* Shown as soon as a file is chosen, which is the only moment a
			    warning is worth anything — after the run starts, the requests
			    are already spent. A VOD's length is not known until its manifest
			    is read, so that path is checked in `run` instead, still before
			    the first OpenRouter call. */}
			{durationSeconds > 0 && (
				<p
					className="mt-4 text-xs"
					style={{
						color:
							account?.isFreeTier &&
							estimateRequests(durationSeconds) > FREE_DAILY_REQUESTS
								? "var(--cs-warn)"
								: "var(--cs-muted)",
					}}
				>
					{Math.round(durationSeconds / 60)} minutes — about{" "}
					{estimateRequests(durationSeconds)} OpenRouter requests.
					{account?.isFreeTier &&
						estimateRequests(durationSeconds) > FREE_DAILY_REQUESTS &&
						` That is more than a free account's ${FREE_DAILY_REQUESTS} a day, so it would stop partway through.`}
				</p>
			)}

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
					audioPerHour: null,
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
						{/* Price is shown for chat models only. Transcription models
						    are billed per second or per hour depending on the
						    provider — the SAME model is per-hour on Groq and
						    per-second on DeepInfra — so rendering their number as
						    "$/M tokens" would be a confident lie. */}
						{m.isTranscription
							? m.audioPerHour !== null &&
								` — ~$${m.audioPerHour.toFixed(3)}/hour of audio`
							: m.promptPerM !== null && ` — $${m.promptPerM.toFixed(2)}/M in`}
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
	spent,
}: {
	label: string;
	done: number;
	total: number;
	spent: number;
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

			{/* What OpenRouter says this has ACTUALLY cost, from `usage.cost` on
			    each response. The figure in the model picker is an estimate —
			    OpenRouter reports speech pricing in units it does not name — so
			    this is the number to trust, and it is worth showing while the
			    run is happening rather than after the money is gone. */}
			{spent > 0 && (
				<p className="mt-2 text-xs" style={{ color: "var(--cs-muted)" }}>
					Spent so far: ${spent.toFixed(4)}
				</p>
			)}
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
				<a href={LINKS.donate} className="cs-btn-quiet px-5 py-2.5 text-sm">
					Donate
				</a>
			</div>

			{/* Word for word what the desktop app and the website say. Copied,
			    not rewritten — see the note in content.ts. */}
			<div className="cs-card mt-6 p-4">
				<p className="text-sm font-semibold">{DONATE_TITLE}</p>
				<p
					className="mt-1 text-sm leading-relaxed"
					style={{ color: "var(--cs-muted)" }}
				>
					{DONATE_NOTE}
				</p>
			</div>

			<p className="mt-4 text-xs" style={{ color: "var(--cs-muted)" }}>
				Needs Windows and 16 GB of RAM. An NVIDIA graphics card makes it much
				faster, but it runs without one.
			</p>
		</section>
	);
}
