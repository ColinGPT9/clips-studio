import type { MetadataRoute } from "next";
import { SITE_URL } from "@/lib/content";

/** Generates a static `sitemap.xml` at build time.
 *
 *  One page, because there is one page — `/callback` is an OAuth handshake,
 *  not a destination. A sitemap this small earns its place mainly by giving
 *  Search Console something concrete to accept, which is a faster route to
 *  being crawled than waiting to be found.
 *
 *  Empty while `SITE_URL` is unset: a sitemap of relative or wrong-domain
 *  URLs is worse than none.
 */
export const dynamic = "force-static";

export default function sitemap(): MetadataRoute.Sitemap {
	if (!SITE_URL) return [];

	return [
		{
			url: SITE_URL.replace(/\/$/, ""),
			lastModified: new Date(),
			changeFrequency: "monthly",
			priority: 1,
		},
	];
}
