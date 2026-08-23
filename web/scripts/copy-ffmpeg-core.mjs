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
const from = join(root, "node_modules", "@ffmpeg", "core", "dist", "umd");
const to = join(root, "public", "ffmpeg");

await mkdir(to, { recursive: true });

for (const name of ["ffmpeg-core.js", "ffmpeg-core.wasm"]) {
	await copyFile(join(from, name), join(to, name));
	console.log(`ffmpeg core: ${name}`);
}
