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
