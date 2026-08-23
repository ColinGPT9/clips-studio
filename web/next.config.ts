/** Clips Kitty Web — a static, client-only bundle.
 *
 *  Deliberately its own Next app rather than a route inside `whop-app/`:
 *  that one is a Whop marketplace app (`withWhopAppConfig`, Whop SDK,
 *  community-embedded routes) and is under review. This shares none of its
 *  concerns, and it needs its own origin anyway — OpenRouter builds an app
 *  page from the `HTTP-Referer` we send, and that identity should be this
 *  tool rather than the Whop listing.
 *
 *  ## No COOP/COEP here, on purpose
 *
 *  ffmpeg.wasm only needs `SharedArrayBuffer` — and therefore cross-origin
 *  isolation — when it runs multi-threaded. Everything this app asks of it is
 *  `-c copy`: remuxing already-encoded video, which is I/O rather than
 *  compute, so threads buy nothing. Skipping the headers avoids breaking
 *  third-party embeds and keeps the app deployable anywhere static.
 *
 *  If anyone later adds re-encoding (vertical crop, burned captions — both
 *  deliberately desktop-only), that changes: it would need `@ffmpeg/core-mt`,
 *  `Cross-Origin-Opener-Policy: same-origin` and
 *  `Cross-Origin-Embedder-Policy: require-corp` added below. Ask first
 *  whether it belongs in the browser at all.
 */

import type { NextConfig } from "next";

const nextConfig: NextConfig = {
	// The whole app is client-side; there is no server to render on and
	// nothing on Vercel that could see a visitor's key even by accident.
	output: "export",

	// Next 16 builds with Turbopack by default, and it resolves ffmpeg.wasm's
	// worker and wasm assets without help. An empty object is the documented
	// way to say "Turbopack, no custom configuration" — leaving it out while
	// a `webpack` key exists is what Next treats as a migration mistake.
	turbopack: {},
};

export default nextConfig;
