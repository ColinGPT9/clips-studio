"""Which site does this link actually belong to?

Each source module used to answer with a substring test — `"kick.com" in
url.lower()`. That is true for `https://www.youtube.com/watch?v=x&ref=kick.com`,
which then went to the Kick downloader and failed with something unhelpful.

The host is a specific part of a URL, so it gets parsed rather than searched
for.
"""

from urllib.parse import urlsplit


def host_matches(url: str, domain: str) -> bool:
    """True when the URL's host is `domain` or a subdomain of it.

    Matches on a label boundary, so "kick.com" accepts "www.kick.com" and
    rejects "kick.com.evil.net" — the trick a plain endswith() falls for.
    """
    if not url:
        return False
    # urlsplit only finds a host after a scheme. People paste bare links, so
    # give it one when it is missing rather than failing them.
    if "//" not in url:
        url = "//" + url.lstrip("/")
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:  # malformed IPv6 literal, bad port
        return False
    domain = domain.lower()
    return host == domain or host.endswith("." + domain)
