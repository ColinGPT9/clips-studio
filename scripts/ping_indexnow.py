"""Tell the IndexNow search engines that the website changed.

    python scripts/ping_indexnow.py              # submit every page
    python scripts/ping_indexnow.py --dry-run    # print the payload, send nothing

A new page is invisible until something crawls it, and waiting for that to
happen on its own takes weeks. IndexNow is a one-request way to say "these URLs
changed, come and look", and Bing acts on it within hours. That matters beyond
Bing itself, because ChatGPT answers from the Bing index: a page nothing has
crawled cannot be cited by an assistant, however good it is.

Google is NOT an IndexNow participant. It said it was evaluating the protocol
in 2021 and never joined, so Google and Gemini still come from Search Console
and ordinary crawling. The participants are Bing, Yandex, Naver, Seznam and
Yep, and submitting to one shares with all of them.

The key is deliberately public. It is hosted as a file on the site, and proving
you can write a file at that address is the whole of the authentication. There
is no secret here and nothing to leak.

The URL list is read from sitemap.xml rather than written out again here, so
there is one answer to "what pages exist" and it stays correct when a page is
added. Run scripts/build_sitemap.py first if pages have changed.
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITEMAP = ROOT / "site" / "sitemap.xml"

HOST = "colingpt9.github.io"
KEY = "eb67c51a9415c8625a7fd0f85a479cbe"

# The site lives in a subdirectory, so the key cannot sit at the host root:
# that root belongs to a different repository. keyLocation exists for exactly
# this, and scopes the key to the URLs underneath it.
KEY_LOCATION = f"https://{HOST}/clips-studio/{KEY}.txt"

ENDPOINT = "https://api.indexnow.org/indexnow"


def urls_from_sitemap() -> list[str]:
    """Every <loc> in the sitemap, in the order it lists them."""
    if not SITEMAP.exists():
        return []
    return re.findall(r"<loc>([^<]+)</loc>", SITEMAP.read_text(encoding="utf-8"))


def submit(urls: list[str]) -> int:
    payload = {"host": HOST, "key": KEY, "keyLocation": KEY_LOCATION, "urlList": urls}
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            code = response.status
    except urllib.error.HTTPError as e:
        code = e.code
    except OSError as e:
        print(f"could not reach IndexNow ({type(e).__name__}). Nothing was submitted.")
        return 1

    # 202 is a success: the key is accepted and queued for validation.
    if code in (200, 202):
        print(f"submitted {len(urls)} urls, HTTP {code}")
        return 0
    if code == 403:
        print(f"HTTP 403: the key file is not readable at {KEY_LOCATION}")
    elif code == 422:
        print("HTTP 422: a url does not belong to this host, or sits above keyLocation")
    elif code == 429:
        print("HTTP 429: too many requests. Submitting on every deploy is enough.")
    else:
        print(f"HTTP {code}: submission refused")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be sent, and send nothing")
    args = ap.parse_args()

    urls = urls_from_sitemap()
    if not urls:
        print(f"no urls in {SITEMAP.relative_to(ROOT)}: run scripts/build_sitemap.py first")
        return 1

    if args.dry_run:
        print(f"POST {ENDPOINT}")
        print(json.dumps(
            {"host": HOST, "key": KEY, "keyLocation": KEY_LOCATION, "urlList": urls},
            indent=2,
        ))
        return 0

    return submit(urls)


if __name__ == "__main__":
    sys.exit(main())
