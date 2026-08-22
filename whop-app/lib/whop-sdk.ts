import { Whop } from "@whop/sdk";

/** The Whop client, built on first use rather than on import.
 *
 *  `new Whop(...)` throws when WHOP_API_KEY is missing, and Next evaluates
 *  every route module while collecting page data at build time. Constructing
 *  it at module scope therefore made `npm run build` fail on any machine
 *  without the secret — including a fresh clone of this public repo and any
 *  CI runner. Deferring it means the key is only required when a request is
 *  actually served, which is the only time it is genuinely needed.
 */
let client: Whop | null = null;

export function whop(): Whop {
	if (!client) {
		client = new Whop({
			appID: process.env.NEXT_PUBLIC_WHOP_APP_ID,
			apiKey: process.env.WHOP_API_KEY,
		});
	}
	return client;
}

/** Run a Whop API call for something the page can do without, and return
 *  null instead of throwing.
 *
 *  Every API call this app makes is decorative — a member's first name, a
 *  community's title. The page's actual job is the requirements and the
 *  download link, and neither needs the API at all.
 *
 *  Unwrapped, an `await` on one of these turns a rate limit, a dropped
 *  connection, a rotated key or an account whose granted permissions differ
 *  from ours into a 500 for the whole route. The visitor loses the download
 *  button so that a heading can say "Alex" instead of "you", which is a
 *  terrible trade and an invisible one until it happens to someone else.
 *
 *  It matters most during app review, which is run on an account that is not
 *  ours and permissions we did not choose.
 */
export async function optional<T>(call: () => Promise<T>): Promise<T | null> {
	try {
		return await call();
	} catch (err) {
		console.error("[whop] optional call failed, rendering without it:", err);
		return null;
	}
}
