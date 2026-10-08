"""The website's developers page (site/developers.html) and the links to it.

It says what the code does: the SDK picture's boxes are the ones in
sdk/python/README.md, every step is marked built and none is coming later,
posting isn't a plugin step (Clips Kitty posts clips itself, and WoopSocial
posts to many sites at once, linked as the site's other pages link it, with
its affiliate note), each building block names what it needs, and the
sentence about which release runs plugins (kept in
docs/developers/versioning.md) is in the hero, the Quickstart and the
questions, in the root README's "Write a plugin" section, in llms.txt and on
the roadmap. It names no real game as our example, claims no sandbox, and
links only to pages that exist in this checkout.

It reads the names of real games from Clips Kitty's own registry, so it is an
engine test, not one of the SDK's own (tests/test_plugin_sdk_*.py).
"""

import html
import json
import re
import sys
import unicodedata
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SDK = ROOT / "sdk" / "python"
for path in (str(ROOT), str(SDK), str(ROOT / "scripts")):
    if path not in sys.path:
        sys.path.insert(0, path)

import build_sitemap  # noqa: E402
from clipskitty_sdk import host  # noqa: E402
from clipskitty_sdk.manifest import INPUTS, PERMISSIONS  # noqa: E402

from plugins import registry  # noqa: E402

SITE = ROOT / "site"
PAGE = SITE / "developers.html"
URL = "https://colingpt9.github.io/clips-studio/developers.html"
REPO = "https://github.com/ColinGPT9/clips-studio"
TUTORIAL = ROOT / "docs" / "developers" / "first-game-pipeline.md"
CAPTION = ("posts when the creator clicks Publish, or on a schedule or automatic posting the creator "
           "switched on")
NO_SANDBOX = ("Every plugin runs on the creator's PC with their rights, like any program; Clips Kitty "
              "doesn't sandbox it. The install screen says what it declares.")
POSTING = ("Posting isn't a plugin step: Clips Kitty posts clips itself, and through WoopSocial it can post to "
           "many sites at once on the creator's own account.")
NO_PUBLISHER = "kind: publisher is refused: plugins don't post."
# The WoopSocial link exactly as the site's other pages have it, and the note that goes with it.
WOOPSOCIAL = ('<a href="https://woopsocial.com/?via=clipskitty" target="_blank" '
              'rel="noopener noreferrer sponsored">WoopSocial</a>')
AFFILIATE_NOTE = ('<p class="note">Affiliate link - Clips Kitty may earn a commission if you sign up through it, '
                  'at no extra cost to you.</p>')
# A sentence that names export or publisher plugins, and one that says they are still to come.
POSTING_PLUGINS = re.compile(r"\bexport\b|kind: publisher|\bpublishers\b|\bpublisher plugins?\b", re.I)
STILL_TO_COME = re.compile(r"(?<!not )\b(?:coming later|later|planned|still to come|coming soon)\b", re.I)


def _suggestion_promise() -> str:
    """The one sentence that says when a suggested edit reaches a clip, as
    the app shows it (ui/src/renderer/src/lib/marketplace.ts)."""
    marketplace = (ROOT / "ui" / "src" / "renderer" / "src" / "lib" / "marketplace.ts").read_text(encoding="utf-8")
    m = re.search(r"export const SUGGESTION_PROMISE =\s*'([^']*)'", marketplace)
    assert m, "SUGGESTION_PROMISE is not in marketplace.ts"
    return m.group(1)


SUGGESTION_PROMISE = _suggestion_promise()


def flat(text: str) -> str:
    return " ".join(text.split())


def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def visible(fragment: str) -> str:
    """The text a reader sees in a piece of HTML, on one line."""
    fragment = re.sub(r"<(script|style)\b.*?</\1>", " ", fragment, flags=re.S)
    return flat(html.unescape(re.sub(r"<[^>]+>", " ", fragment))).replace(" .", ".").replace(" ,", ",")


def section(html_text: str, section_id: str) -> str:
    m = re.search(rf'<section id="{section_id}">(.*?)</section>', html_text, re.S)
    assert m, section_id
    return m.group(1)


def hero(html_text: str) -> str:
    return html_text.split('<div class="hero"', 1)[1].split("<section", 1)[0]


def release_sentence() -> str:
    text = (ROOT / "docs" / "developers" / "versioning.md").read_text(encoding="utf-8")
    return flat(text.split("## Which release runs plugins", 1)[1].strip().split("\n\n", 1)[0])


def readme_section() -> str:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    return text.split("\n## Write a plugin\n", 1)[1].split("\n## ", 1)[0]


def llms_section() -> str:
    text = (SITE / "llms.txt").read_text(encoding="utf-8")
    return text.split("\n## For developers\n", 1)[1].split("\n## ", 1)[0]


def md_anchors(path: Path) -> set[str]:
    """GitHub's anchors for a Markdown page's headings."""
    out = set()
    in_code = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
        m = None if in_code else re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if m:
            slug = re.sub(r"[^\w\- ]", "", m.group(1).strip().lower()).replace(" ", "-")
            n, base = 1, slug
            while slug in out:
                slug, n = f"{base}-{n}", n + 1
            out.add(slug)
    return out


# ---- the page --------------------------------------------------------------------------------


def test_the_page_exists_with_its_title_and_canonical():
    text = page()
    assert "<title>Clips Kitty SDK</title>" in text
    assert f'<link rel="canonical" href="{URL}" />' in text
    assert f'<meta property="og:url" content="{URL}" />' in text
    assert "<h1>Clips Kitty SDK</h1>" in text
    lead = visible(hero(text))
    assert ("Teach Clips Kitty your game. Write a small Python plugin that finds, understands or rates the "
            "moments in a video. Clips Kitty does the rest.") in lead
    buttons = re.findall(r'<a class="btn[^"]*" href="([^"]+)"[^>]*>(.*?)</a>', hero(text))
    assert buttons == [(f"{REPO}/blob/main/docs/developers/first-game-pipeline.md", "Start building")]

    # Its structured data says what the page says.
    data = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', text, re.S).group(1))
    graph = {node["@type"]: node for node in data["@graph"]}
    assert graph["WebPage"]["url"] == URL
    assert graph["WebPage"]["name"] == "Clips Kitty SDK"
    assert graph["BreadcrumbList"]["itemListElement"][-1]["item"] == URL
    faq = section(text, "faq")
    shown = [(visible(q), visible(a)) for q, a in re.findall(r"<h3>(.*?)</h3>\s*<p>(.*?)</p>", faq, re.S)]
    said = [(q["name"], q["acceptedAnswer"]["text"]) for q in graph["FAQPage"]["mainEntity"]]
    assert shown == said
    assert len(shown) == 6


def test_every_page_links_it_in_the_nav_and_footer():
    pages = sorted(SITE.glob("*.html"))
    assert len(pages) >= 32
    for p in pages:
        text = p.read_text(encoding="utf-8")
        nav = re.search(r'<nav class="site" aria-label="Main">(.*?)</nav>', text, re.S).group(1)
        links = re.findall(r'<a [^>]*href="([^"]+)"[^>]*>', nav)
        assert links.count("developers.html") == 1, p.name
        assert links[links.index("developers.html") - 1] == "changelog.html", p.name
        current = '<a href="developers.html" aria-current="page">Developers</a>'
        plain = '<a href="developers.html">Developers</a>'
        assert (current if p == PAGE else plain) in nav, p.name
        footer = re.search(r'<nav aria-label="Footer">(.*?)</nav>', text, re.S).group(1)
        assert '<a href="developers.html">Build a plugin</a>' in footer, p.name


def _readme_picture() -> tuple[list, list]:
    """The boxes and steps of the SDK picture, as sdk/python/README.md draws it."""
    text = (SDK / "README.md").read_text(encoding="utf-8")
    block = text.split("```text\nClips Kitty SDK\n", 1)[1].split("```", 1)[0]
    boxes, steps = [], []
    for line in block.splitlines():
        if not line.strip() or line.strip() == "↓":
            continue
        step = re.match(r"^\s*[├└]── (.*)$", line)
        parts = re.split(r"\s{2,}", (step.group(1) if step else line).strip())
        (steps if step else boxes).append(tuple(parts))
    return boxes, steps


def _page_picture() -> tuple[list, list]:
    text = section(page(), "how-it-fits")
    boxes = re.findall(r'<li class="box[^"]*"><strong>(.*?)</strong><span>(.*?)</span>', text)
    steps = re.findall(r'<li class="step( later)?"><code>(.*?)</code><span class="what">(.*?)</span>'
                       r'<em class="tag (built|later)">(.*?)</em></li>', text)
    return boxes, steps


def test_every_step_is_built_and_posting_is_not_a_plugin_step():
    boxes, steps = _page_picture()
    want_boxes, want_steps = _readme_picture()
    assert [tuple(map(html.unescape, b)) for b in boxes] == want_boxes
    assert [(name, what, label) for _, name, what, _, label in steps] == want_steps
    state = {name: (later, tag, label) for later, name, _, tag, label in steps}
    assert state == {
        "find": ("", "built", "built"),
        "understand": ("", "built", "built"),
        "rate": ("", "built", "built"),
        "edit": ("", "built", "built"),
    }
    fits = visible(section(page(), "how-it-fits"))
    assert "Clips Kitty SDK" in fits
    assert "Edit plugins suggest edits that wait for the creator in the editor" in fits
    assert POSTING in fits and NO_PUBLISHER in fits
    faq = visible(section(page(), "faq"))
    assert "What about edit and posting? Edit is built, and posting isn't a plugin step." in faq
    assert "Edit plugins suggest edits that wait for the creator in the editor" in faq
    assert SUGGESTION_PROMISE in faq
    assert ("Clips Kitty posts clips itself, and through WoopSocial it can post to many sites at once on the "
            "creator's own account. A manifest that asks for a publisher (kind: publisher) is refused: plugins "
            "don't post.") in faq


def _promises_posting_plugins(text: str) -> list[str]:
    """The sentences of some text that name export or publisher plugins and say they are still to come."""
    sentences = re.split(r"(?<=[.;!?])\s+", flat(text))
    return [s for s in sentences if POSTING_PLUGINS.search(s) and STILL_TO_COME.search(s)]


def test_no_page_says_export_or_publisher_plugins_are_coming():
    roadmap = (SITE / "roadmap.html").read_text(encoding="utf-8")
    texts = {
        "site/developers.html": page(),
        "README.md, Write a plugin": readme_section(),
        "site/llms.txt, For developers": llms_section(),
        "site/roadmap.html": roadmap,
    }
    for where, text in texts.items():
        # What a reader sees, and what search engines read in its attributes and data.
        for said in (visible(text), flat(html.unescape(text))):
            assert not _promises_posting_plugins(said), (where, _promises_posting_plugins(said))
    assert "coming later" not in flat(html.unescape(page())).lower()
    readme = flat(readme_section()).replace("`", "")
    assert POSTING.removesuffix(".") + " ([Publish to every platform at once](#publish-to-every-platform-at-once))." in readme
    assert NO_PUBLISHER in readme
    llms = flat(llms_section())
    assert "Posting isn't a plugin step: Clips Kitty posts clips itself, and through WoopSocial" in llms
    assert "coming later" not in llms


def test_the_woopsocial_link_is_the_sites_own_with_its_note():
    text = page()
    tags = [tag for tag in re.findall(r"<a\b[^>]*>.*?</a>", text, re.S) if "woopsocial" in tag.lower()]
    assert tags == [WOOPSOCIAL]
    # The same link, word for word, that the home page and the roadmap already carry.
    for other in ("index.html", "roadmap.html"):
        assert WOOPSOCIAL in (SITE / other).read_text(encoding="utf-8"), other
    # It sits in How a plugin fits in, with the affiliate note the site puts beside it.
    fits = section(text, "how-it-fits")
    assert WOOPSOCIAL in fits
    assert fits.index(WOOPSOCIAL) < fits.index(AFFILIATE_NOTE)
    assert text.count(AFFILIATE_NOTE) == 1
    # Only that one WoopSocial address, anywhere on the page.
    addresses = set(re.findall(r"https?://[^\s\"<>]*woopsocial[^\s\"<>]*", text))
    assert addresses == {"https://woopsocial.com/?via=clipskitty"}


def test_the_caption_is_exact():
    boxes, _ = _page_picture()
    assert boxes[-1] == ("Creator / Social Platform", CAPTION)
    assert CAPTION in visible(page())
    assert CAPTION in readme_section()


# ---- what it doesn't say ---------------------------------------------------------------------


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    return " " + re.sub(r"[^a-z0-9]+", " ", text).strip() + " "


def real_games() -> set[str]:
    """Names and short names of real games: the catalog's game sections and the search's aliases."""
    sections = yaml.safe_load((ROOT / "awesome-clips-kitty" / "registry" / "sections.yaml").read_text(encoding="utf-8"))
    names = set()

    def walk(node):
        if isinstance(node, dict):
            sid = node.get("id")
            if isinstance(sid, str) and sid.startswith("gaming/") and sid != "gaming/generic":
                names.add(sid.split("/", 1)[1])
                names.add(str(node.get("title") or ""))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(sections)
    for short, long in registry.ALIASES.items():
        if short not in {"football", "soccer", "yt"}:
            names |= {short, long}
    return {_norm(n).strip() for n in names if n.strip()}


CLAIMS = (
    re.compile(r"\bplugins?\b[^.;]{0,40}\b(?:can't|cannot|can not|isn't able to|is not able to|is unable to)\b"
               r"[^.;]{0,40}\b(?:post|publish|install|remove|uninstall)", re.I),
    re.compile(r"\b(?:is|are) (?:fully |completely )?sandboxed\b", re.I),
    re.compile(r"\bruns? in(?:side)? a sandbox\b", re.I),
    re.compile(r"\bsandboxed (?:plugin|process)", re.I),
    re.compile(r"\bposts? only when the creator clicks\b", re.I),
    re.compile(r"\bplugins? (?:work|run)s? (?:today|now)\b", re.I),
    re.compile(r"\bnext release\b", re.I),
)


def test_it_names_no_real_game_and_claims_no_sandbox():
    games = real_games()
    assert {"marvel rivals", "world of warcraft", "wow", "minecraft"} <= games
    texts = {
        "site/developers.html": page(),
        "README.md, Write a plugin": readme_section(),
        "site/llms.txt, For developers": llms_section(),
    }
    for where, text in texts.items():
        words = _norm(text)
        assert not [game for game in games if f" {game} " in words], where
        # What a reader sees, and what search engines read in its attributes and data.
        found = [m.group(0) for claim in CLAIMS for said in (visible(text), flat(html.unescape(text)))
                 for m in claim.finditer(said)]
        assert not found, f"{where}: {found}"
    # It says the true thing instead, in the creators' section and the questions.
    assert NO_SANDBOX in visible(section(page(), "what-creators-see"))
    assert "Is a plugin sandboxed? No." in visible(section(page(), "faq"))
    assert "Quarkbloom Arena, a made-up game" in visible(section(page(), "quickstart"))


def _md_target_ok(rel: str, anchor: str) -> bool:
    path = ROOT / rel
    if not path.is_file():
        return False
    return not anchor or path.suffix != ".md" or anchor in md_anchors(path)


def check_url(url: str) -> str | None:
    """Why `url` isn't one this checkout backs, or None when it is."""
    if url == "https://schema.org":
        return None
    if url == "https://paypal.me/clipsstudio":
        return None  # the donate link every page already has
    if url == "https://woopsocial.com/?via=clipskitty":
        return None  # the WoopSocial link the home page, the roadmap and the privacy policy already have
    if url.startswith("https://colingpt9.github.io/clips-studio/"):
        rel = url.removeprefix("https://colingpt9.github.io/clips-studio/") or "index.html"
        return None if (SITE / rel).is_file() else f"no site/{rel}"
    if url in (REPO, f"{REPO}/discussions", f"{REPO}/issues"):
        return None
    if url.startswith(f"{REPO}#subdirectory="):
        # pip's form, in the install line: the folder must hold a package to install.
        folder = url.split("=", 1)[1]
        return None if (ROOT / folder / "pyproject.toml").is_file() else f"no {folder}/pyproject.toml"
    if url.startswith(f"{REPO}#"):
        return None if url.split("#", 1)[1] in md_anchors(ROOT / "README.md") else "no such README heading"
    if url.startswith(f"{REPO}/blob/main/"):
        rel, _, anchor = url.removeprefix(f"{REPO}/blob/main/").partition("#")
        return None if _md_target_ok(rel, anchor) else f"no {rel}#{anchor} in this checkout"
    return "not an address this checkout backs"


def test_its_links_resolve_and_external_ones_open_safely():
    text = page()
    for tag in re.findall(r"<a\b[^>]*>", text):
        href = re.search(r'href="([^"]+)"', tag).group(1)
        if href.startswith(("http://", "https://")):
            assert 'target="_blank"' in tag, tag
            rel = re.search(r'rel="([^"]+)"', tag).group(1).split()
            assert {"noopener", "noreferrer"} <= set(rel), tag
        else:
            assert not href.startswith("/"), tag
            assert (SITE / href.split("#")[0]).is_file(), tag
    for src in re.findall(r'src="([^"]+)"', text):
        assert (SITE / src).is_file(), src
    problems = [f"{url}: {why}" for url in sorted(set(re.findall(r'https?://[^"\s<>)]+', text)))
                if (why := check_url(url))]
    assert not problems, "\n".join(problems)

    # The README's new section and llms.txt's link only to what exists too.
    readme = readme_section()
    for target in re.findall(r"\]\(([^)\s]+)\)", readme):
        if target.startswith("http"):
            assert check_url(target) is None, target
        elif target.startswith("#"):
            assert target[1:] in md_anchors(ROOT / "README.md"), target
        else:
            path, _, anchor = target.partition("#")
            assert (ROOT / path).is_file(), target
            assert not anchor or anchor in md_anchors(ROOT / path), target
    for target in re.findall(r"\]\((https?://[^)\s]+)\)", llms_section()):
        assert check_url(target) is None, target


def test_llms_txt_and_the_sitemap_list_it():
    llms = llms_section()
    assert f"[Clips Kitty SDK]({URL})" in llms
    assert release_sentence() in flat(llms)
    sitemap = (SITE / "sitemap.xml").read_text(encoding="utf-8")
    entry = re.search(rf"<url>\s*<loc>{re.escape(URL)}</loc>\s*<lastmod>[\d-]+</lastmod>\s*"
                      r"<priority>([\d.]+)</priority>\s*</url>", sitemap)
    assert entry and entry.group(1) == "0.7"
    assert build_sitemap.PRIORITY["developers.html"] == "0.7"

    # The committed sitemap lists the pages build_sitemap.py would, with the same
    # priorities. The dates come from git, so CI's `build_sitemap.py --check` checks those.
    def pages(xml: str) -> list[tuple[str, str]]:
        return re.findall(r"<loc>(.*?)</loc>\s*<lastmod>[\d-]+</lastmod>\s*<priority>(.*?)</priority>", xml)

    assert pages(sitemap) == pages(build_sitemap.build())


def test_it_says_which_release_runs_plugins():
    sentence = release_sentence()
    assert sentence.startswith("No Clips Kitty release runs plugins yet.")
    text = page()
    for where, part in (("hero", hero(text)), ("quickstart", section(text, "quickstart")),
                        ("faq", section(text, "faq"))):
        assert sentence in visible(part), where
    assert sentence.removesuffix(".") in flat(readme_section())
    assert "([From source](#from-source))" in readme_section()
    assert f"{REPO}#from-source" in section(text, "faq")


def test_each_building_block_states_its_own_needs():
    text = page()
    blocks = section(text, "building-blocks")
    for where in (text, readme_section(), llms_section(), (ROOT / "README.md").read_text(encoding="utf-8")):
        assert "standard library and ffmpeg only" not in flat(where).lower()
    needs = dict(re.findall(r'<div class="card" id="block-([\w-]+)">.*?<p class="needs">(.*?)</p>', blocks, re.S))
    needs = {name: visible(line) for name, line in needs.items()}
    assert set(needs) == {"media", "signals", "text", "local-model", "testing"}
    # Each block is a module the SDK has.
    for name in needs:
        assert (SDK / "clipskitty_sdk" / f"{name.replace('-', '_')}.py").is_file(), name
    assert len(set(needs.values())) == len(needs), "each block states its own needs"
    # Every permission and input a block names is a real one.
    for line in needs.values():
        for word in re.findall(r"\b(?:video\.read|transcript\.read|ffmpeg|ollama|gpu|network)\b", line):
            assert word in PERMISSIONS, word
    assert "ffmpeg" in needs["media"] and "video.read" in needs["media"]
    assert "nothing of its own" in needs["signals"]
    assert "transcript.read" in needs["text"] and "transcript" in INPUTS
    local = needs["local-model"]
    assert "ollama" in local and "local model" in local and "this PC" in local
    assert "cloud provider" in local and "proxy" in local
    assert "test extra" in needs["testing"] and "not in the app" in needs["testing"]


def test_the_quickstart_is_the_tutorials():
    """The three commands (on the page and in the README) are the tutorial's,
    which tests/test_plugin_docs.py runs, and follow the docs' PowerShell rules."""
    tutorial = TUTORIAL.read_text(encoding="utf-8")
    page_blocks = re.findall(r'<pre><code class="language-(powershell|bash)">(.*?)</code></pre>',
                             section(page(), "quickstart") + section(page(), "get-listed"), re.S)
    readme_blocks = re.findall(r"```(powershell|bash)\n(.*?)```", readme_section(), re.S)
    assert len(page_blocks) == 4 and len(readme_blocks) == 2
    for shell, body in [*page_blocks, *readme_blocks]:
        body = html.unescape(body)
        tutorial_lines = set()
        for block in re.findall(rf"```{shell}\n(.*?)```", tutorial, re.S):
            tutorial_lines |= set(block.splitlines())
        for line in body.strip().splitlines():
            assert line in tutorial_lines, (shell, line)
            if shell == "powershell":
                assert line.startswith("py -m "), line
                assert "&&" not in line
                assert "," not in re.sub(r'"[^"]*"', "", line), line
            else:
                assert line.startswith("python -m "), line


def test_it_names_clips_kittys_python():
    version = ".".join(map(str, host.APP_PYTHON))
    text = visible(page())
    assert f"Python {version}" in text
    assert f"Test on {version}" in text
    assert f"Python {version}" in readme_section() and f"Python {version}" in llms_section()
    assert not re.search(r"Python 3\.(?!" + re.escape(str(host.APP_PYTHON[1])) + r"\b)\d+", text)


def test_the_roadmap_no_longer_lists_plugins_as_future_only():
    roadmap = (SITE / "roadmap.html").read_text(encoding="utf-8")
    sections = re.findall(r"<section>(.*?)</section>", roadmap, re.S)
    later = [s for s in sections if "<h2>Later</h2>" in s]
    assert len(later) == 1
    later_text = visible(later[0]).lower()
    assert "plugin architecture" not in later_text
    assert "community extensions" not in later_text
    assert "export" not in later_text
    assert "edit and export" not in later_text and "plugins that edit" not in later_text
    assert "creator analytics" in later_text and "more models" in later_text

    item = re.search(r'<li class="partial" id="plugins">(.*?)</li>', roadmap, re.S)
    assert item, "the plugins item is in the roadmap's Now section"
    assert item.group(0) not in later[0]
    said = visible(item.group(1))
    assert ("Plugins that find, understand and rate the moments in a video are built, and so are plugins "
            "that suggest edits for the clips, which wait for you in the editor") in said
    assert release_sentence() in said
    assert 'href="developers.html"' in item.group(1)
