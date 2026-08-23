import type { MetadataRoute } from "next";
import { SITE_URL } from "@/lib/content";

/** Generates a static `robots.txt` at build time.
 *
 *  Mirrors the `robots` metadata in `layout.tsx` rather than duplicating the
 *  decision: both read `SITE_URL`, so there is one switch and no way for the
 *  meta tag and the robots file to disagree — which is the usual way a site
 *  ends up half-indexed.
 *
 *  While `SITE_URL` is empty this disallows everything, deliberately. See the
 *  note on that constant: indexing the `*.vercel.app` address before a real
 *  domain exists means the ranking lands on a URL that has to be retired
 *  later.
 *
 *  `/callback` is always excluded. It exists for a few hundred milliseconds
 *  during an OAuth handshake, carries a single-use code in its query string,
 *  and has nothing to say to a reader.
 */
export const dynamic = "force-static";

export default function robots(): MetadataRoute.Robots {
	if (!SITE_URL) {
		return { rules: [{ userAgent: "*", disallow: "/" }] };
	}

	return {
		rules: [{ userAgent: "*", allow: "/", disallow: "/callback" }],
		sitemap: `${SITE_URL.replace(/\/$/, "")}/sitemap.xml`,
	};
}
