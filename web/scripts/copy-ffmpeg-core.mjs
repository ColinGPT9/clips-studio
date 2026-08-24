/** Copy the ffmpeg.wasm core into public/ so it is served from our own origin.
 *
 *  The alternative is loading it from unpkg at runtime, which every
 *  ffmpeg.wasm tutorial does and which quietly makes the tool depend on a CDN
 *  staying up, staying fast, and not being blocked by whatever network the
 *  visitor is on. A ~30 MB dependency is worth owning.
 *
 *  Runs on postinstall and again before build, because Vercel installs and
 *  builds in one shot and a missing core only shows up as a broken cut long
 *  after deploy.
 */

import { copyFile, mkdir } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");

/** ESM, and it MUST be ESM. Do not "simplify" this to umd.
 *
 *  `@ffmpeg/ffmpeg` creates its worker with `type: "module"`
 *  (classes.js:104-112, unconditionally). A module worker has no
 *  `importScripts`, so the loader falls through to a dynamic `import()` and
 *  reads `.default` off it. The UMD build has no ES export — it ends in
 *  `module.exports` / `define` / `exports[...]` — so `.default` is undefined,
 *  it overwrites `self.createFFmpegCore` with that undefined, and throws
 *  `failed to import ffmpeg-core.js`.
 *
 *  The loader does try to rescue this, but only by rewriting `/umd/` to
 *  `/esm/` when the core URL is its own default. Ours is served from
 *  `/ffmpeg/`, so the rescue never fires.
 *
 *  Shipping umd meant ffmpeg never loaded at all, which broke every path in
 *  the app — file uploads and VOD links alike — and reported it as
 *  "Something went wrong", because the library stringifies worker errors and
 *  the page only rendered `Error` instances.
 *
 *  The .wasm is byte-identical between the two builds; only this wrapper
 *  differs. */
const from = join(root, "node_modules", "@ffmpeg", "core", "dist", "esm");
const to = join(root, "public", "ffmpeg");

await mkdir(to, { recursive: true });

for (const name of ["ffmpeg-core.js", "ffmpeg-core.wasm"]) {
	await copyFile(join(from, name), join(to, name));
	console.log(`ffmpeg core: ${name}`);
}
