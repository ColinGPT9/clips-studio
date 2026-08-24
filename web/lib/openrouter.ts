/** Everything that talks to OpenRouter, from the visitor's browser only.
 *
 *  The rule this file exists to enforce: **no key ever reaches our server.**
 *  The visitor signs in with their own OpenRouter account, the key that comes
 *  back is theirs, it lives in their localStorage, and every request below is
 *  made by their browser directly to openrouter.ai. Nothing here runs on
 *  Vercel, so there is no server-side path a key could leak through.
 *
 *  That is not a stylistic choice. It is what lets this page exist at all:
 *  inference is billed to the visitor, so a free tool costs the project
 *  nothing, and a 2-hour VOD is ~24 sequential calls that would blow any
 *  serverless timeout but is merely a loop in a tab.
 *
 *  Two OpenRouter quirks worth knowing before editing:
 *
 *  1. `HTTP-Referer` is deliberately NOT the standard `Referer` header, which
 *     browsers forbid scripts from setting. OpenRouter picked a non-standard
 *     name precisely so browser apps can attribute themselves, and their CORS
 *     allow-list includes it. This is what creates our app page in their
 *     marketplace, and it counts traffic to us regardless of whose key paid.
 *  2. STT pricing is reported in DIFFERENT UNITS PER PROVIDER for the same
 *     model (Groq bills whisper-large-v3-turbo per hour, DeepInfra per
 *     second). Never compute a cost estimate by multiplying blindly.
 */

const BASE = "https://openrouter.ai/api/v1";
const AUTH_URL = "https://openrouter.ai/auth";

const KEY_STORAGE = "clipskitty-openrouter-key";
const VERIFIER_STORAGE = "clipskitty-openrouter-verifier";

/** Sent on every inference call.
 *
 *  `HTTP-Referer` must be the production origin: OpenRouter excludes
 *  localhost traffic from the marketplace, so a dev run will never create the
 *  app page no matter what else is right.
 *
 *  `video-gen` is the closest category OpenRouter offers. It means
 *  *generation* rather than editing, which is not quite us, but the category
 *  list is fixed and unrecognised values are dropped silently. */
function attribution(): Record<string, string> {
	return {
		"HTTP-Referer": typeof window === "undefined" ? "" : window.location.origin,
		"X-OpenRouter-Title": "Clips Kitty Web",
		"X-OpenRouter-Categories": "video-gen",
	};
}

// ---- the key -------------------------------------------------------------

export function loadKey(): string | null {
	try {
		return window.localStorage.getItem(KEY_STORAGE);
	} catch {
		// Private windows and blocked site data throw on access rather than
		// returning null. Signed-out is the correct reading of that.
		return null;
	}
}

export function saveKey(key: string): void {
	try {
		window.localStorage.setItem(KEY_STORAGE, key);
	} catch {
		// Non-fatal: the key stays in memory for this session instead.
	}
}

export function clearKey(): void {
	try {
		window.localStorage.removeItem(KEY_STORAGE);
	} catch {
		/* nothing to clear */
	}
}

// ---- OAuth PKCE ----------------------------------------------------------

function base64url(bytes: ArrayBuffer): string {
	return btoa(String.fromCharCode(...new Uint8Array(bytes)))
		.replace(/\+/g, "-")
		.replace(/\//g, "_")
		.replace(/=+$/, "");
}

/** Send the visitor to OpenRouter to authorise this app.
 *
 *  No client_id and no app registration: OpenRouter's flow identifies the app
 *  after the fact by `HTTP-Referer`, which means there is no secret of ours
 *  embedded in a page anyone can read. The verifier is held in sessionStorage
 *  (not local) so it dies with the tab — it is single-use and worthless after
 *  the exchange. */
export async function beginSignIn(callbackUrl: string): Promise<void> {
	const raw = new Uint8Array(32);
	crypto.getRandomValues(raw);
	const verifier = base64url(raw.buffer);

	const digest = await crypto.subtle.digest(
		"SHA-256",
		new TextEncoder().encode(verifier),
	);

	window.sessionStorage.setItem(VERIFIER_STORAGE, verifier);

	const url = new URL(AUTH_URL);
	url.searchParams.set("callback_url", callbackUrl);
	url.searchParams.set("code_challenge", base64url(digest));
	url.searchParams.set("code_challenge_method", "S256");
	window.location.href = url.toString();
}

/** Exchange the returned code for the visitor's own API key.
 *
 *  Codes are single-use and expire after 10 minutes, so a stale one landing
 *  here (a refreshed callback page, a bookmarked URL) is normal rather than
 *  exceptional — it must read as "sign in again", not as a crash. */
export async function completeSignIn(code: string): Promise<string> {
	const verifier = window.sessionStorage.getItem(VERIFIER_STORAGE);
	if (!verifier) {
		throw new Error(
			"This sign-in link has expired. Please start the sign-in again.",
		);
	}

	const res = await fetch(`${BASE}/auth/keys`, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({
			code,
			code_verifier: verifier,
			code_challenge_method: "S256",
		}),
	});

	window.sessionStorage.removeItem(VERIFIER_STORAGE);

	if (!res.ok) {
		throw new Error(
			res.status === 403
				? "That sign-in link has already been used or has expired. Please sign in again."
				: `OpenRouter refused the sign-in (HTTP ${res.status}).`,
		);
	}

	const { key } = (await res.json()) as { key?: string };
	if (!key) throw new Error("OpenRouter returned no key.");

	saveKey(key);
	return key;
}

/** Where the visitor manages spend limits and revocation.
 *
 *  This app cannot cap what the key it receives is allowed to spend — the
 *  exchange endpoint takes no limit parameter and limits are purely
 *  account-side. Linking here is the honest substitute. The page only opens
 *  for the signed-in owner of that key. */
export async function keySettingsUrl(key: string): Promise<string> {
	const digest = await crypto.subtle.digest(
		"SHA-256",
		new TextEncoder().encode(key),
	);
	const hex = Array.from(new Uint8Array(digest))
		.map((b) => b.toString(16).padStart(2, "0"))
		.join("");
	return `https://openrouter.ai/keys/${hex}`;
}

// ---- errors --------------------------------------------------------------

/** Turn an OpenRouter failure into something a creator can act on.
 *
 *  The status codes that actually happen here each have a different remedy,
 *  and "Request failed with status 402" tells someone none of them. */
async function explain(res: Response): Promise<Error> {
	let detail = "";
	try {
		const body = await res.json();
		detail = body?.error?.message ?? "";
	} catch {
		/* a non-JSON error body tells us nothing extra */
	}

	const message =
		res.status === 401
			? "OpenRouter rejected your sign-in. Try signing in again."
			: res.status === 402
				? "Your OpenRouter account is out of credits. Add credits and try again."
				: res.status === 429
					? "OpenRouter is rate-limiting you. Free accounts get 50 requests a day until you have bought $10 of credit — after that it is 1000."
					: res.status === 403
						? "The provider refused this content. Try a different model, or use the desktop app, which runs the AI locally with no content filter."
						: `OpenRouter returned HTTP ${res.status}.`;

	return new Error(detail ? `${message} (${detail})` : message);
}

// ---- account -------------------------------------------------------------

export type KeyInfo = {
	/** True until the account has EVER purchased $10 of credit. Gates 50
	 *  requests/day versus 1000, which is the difference between "one short
	 *  VOD" and "a working day". */
	isFreeTier: boolean;
	usage: number;
	limitRemaining: number | null;
};

export async function keyInfo(key: string): Promise<KeyInfo> {
	const res = await fetch(`${BASE}/key`, {
		headers: { Authorization: `Bearer ${key}` },
	});
	if (!res.ok) throw await explain(res);

	const { data } = await res.json();
	return {
		isFreeTier: Boolean(data?.is_free_tier),
		usage: Number(data?.usage ?? 0),
		limitRemaining:
			data?.limit_remaining === null || data?.limit_remaining === undefined
				? null
				: Number(data.limit_remaining),
	};
}

export type Model = {
	id: string;
	name: string;
	contextLength: number;
	/** USD per million tokens, or null where OpenRouter reports the `-1`
	 *  sentinel it uses for routers whose price varies by whatever they pick. */
	promptPerM: number | null;
	completionPerM: number | null;
	/** Used to keep models that cannot honour `response_format` out of the
	 *  picker. Scoring parses the reply as JSON, so a model without it is not
	 *  a worse choice here — it is a broken one. */
	supportsJson: boolean;
	/** Transcription models are a different list from chat models and must
	 *  never appear in the same dropdown. */
	isTranscription: boolean;
};

/** Models this visitor can actually use.
 *
 *  `/models/user` rather than `/models`: it already applies their own privacy
 *  settings and provider preferences, so we never offer a model their account
 *  would refuse to route to. */
export async function listModels(key: string): Promise<Model[]> {
	const res = await fetch(`${BASE}/models/user`, {
		headers: { Authorization: `Bearer ${key}` },
	});
	if (!res.ok) throw await explain(res);

	const { data } = await res.json();
	return (data ?? [])
		.map((m: Record<string, unknown>) => {
			const pricing = (m.pricing ?? {}) as Record<string, string>;
			const perM = (raw: string | undefined): number | null => {
				const n = Number(raw);
				return !raw || Number.isNaN(n) || n < 0 ? null : n * 1_000_000;
			};
			const params = Array.isArray(m.supported_parameters)
				? (m.supported_parameters as string[])
				: [];
			const outputs = ((m.architecture ?? {}) as Record<string, unknown>)
				.output_modalities;

			return {
				id: String(m.id),
				name: String(m.name ?? m.id),
				contextLength: Number(m.context_length ?? 0),
				promptPerM: perM(pricing.prompt),
				completionPerM: perM(pricing.completion),
				supportsJson:
					params.includes("response_format") ||
					params.includes("structured_outputs"),
				isTranscription:
					Array.isArray(outputs) && outputs.includes("transcription"),
			};
		})
		.filter((m: Model) => m.id);
}

// ---- transcription -------------------------------------------------------

export type Segment = { start: number; end: number; text: string };

/** Transcribe one audio chunk.
 *
 *  `offsetSeconds` shifts every returned timestamp back onto the original
 *  recording's clock. Chunks are cut at ~60s because upstream providers time
 *  out after 60 seconds of PROCESSING — that limit binds long before the
 *  25 MB upload cap does, so size is never the reason to split. Get this
 *  wrong and every clip after the first chunk is cut in the wrong place. */
export async function transcribeChunk(
	key: string,
	audio: Blob,
	model: string,
	offsetSeconds: number,
): Promise<Segment[]> {
	const form = new FormData();
	// The extension is how the endpoint infers the format, so it has to match
	// what `segmentAudio` actually produces — which is copied AAC in an MP4
	// container, not the decoded WAV this used to send.
	form.append("file", audio, "chunk.m4a");
	form.append("model", model);
	form.append("response_format", "verbose_json");

	const res = await fetch(`${BASE}/audio/transcriptions`, {
		method: "POST",
		headers: { Authorization: `Bearer ${key}`, ...attribution() },
		body: form,
	});
	if (!res.ok) throw await explain(res);

	const body = await res.json();

	// `verbose_json` is only honoured by OpenAI-compatible providers; anything
	// else returns plain `{text}`. One un-timed segment covering the chunk is
	// a usable fallback — scoring still works, the boundaries are just coarse.
	if (!Array.isArray(body?.segments)) {
		const text = String(body?.text ?? "").trim();
		return text
			? [{ start: offsetSeconds, end: offsetSeconds + 60, text }]
			: [];
	}

	return body.segments
		.map((s: Record<string, unknown>) => ({
			start: Number(s.start ?? 0) + offsetSeconds,
			end: Number(s.end ?? 0) + offsetSeconds,
			text: String(s.text ?? "").trim(),
		}))
		.filter((s: Segment) => s.text);
}

// ---- generation ----------------------------------------------------------

/** One completion.
 *
 *  Mirrors `LLMBackend.generate()` in the desktop app deliberately: same
 *  single-string-in, single-string-out shape, so the scoring code ported from
 *  `analysis/highlights.py` needs no reshaping and the two stay comparable.
 *
 *  `provider` is the part worth not deleting. `data_collection: "deny"` and
 *  `zdr: true` keep someone's stream transcript away from providers that
 *  retain or train on it — a guarantee made in code rather than left to each
 *  visitor's account settings. `require_parameters` stops OpenRouter routing
 *  to a provider that would quietly ignore `response_format` and hand back
 *  prose where the caller is about to run JSON.parse. */
export async function generate(
	key: string,
	model: string,
	prompt: string,
	{ jsonMode = false }: { jsonMode?: boolean } = {},
): Promise<string> {
	const body: Record<string, unknown> = {
		model,
		messages: [{ role: "user", content: prompt }],
		provider: {
			data_collection: "deny",
			zdr: true,
			require_parameters: true,
		},
	};
	if (jsonMode) body.response_format = { type: "json_object" };

	const res = await fetch(`${BASE}/chat/completions`, {
		method: "POST",
		headers: {
			Authorization: `Bearer ${key}`,
			"Content-Type": "application/json",
			...attribution(),
		},
		body: JSON.stringify(body),
	});
	if (!res.ok) throw await explain(res);

	const data = await res.json();
	return String(data?.choices?.[0]?.message?.content ?? "");
}
