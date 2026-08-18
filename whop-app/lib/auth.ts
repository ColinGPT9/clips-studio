import { headers } from "next/headers";
import { whop } from "@/lib/whop-sdk";

/** The verified Whop user, or null when there is no valid token.
 *
 *  Whop sends a short-lived JWT in `x-whop-user-token` on requests to the
 *  app's own origin, and it is only trustworthy after `verifyUserToken`
 *  checks its signature and expiry — which is why that call is server-side
 *  and this helper is never used from a client component.
 *
 *  Returning null rather than throwing lets a page render a "open this from
 *  inside Whop" notice instead of a 500. That still fails closed: without a
 *  verified id we make no Whop API calls and show no user or company data.
 *  It matters for review, where the app is opened in ways a member never
 *  would.
 */
export async function verifiedUserId(): Promise<string | null> {
	try {
		const { userId } = await whop().verifyUserToken(await headers());
		return userId ?? null;
	} catch {
		return null;
	}
}
