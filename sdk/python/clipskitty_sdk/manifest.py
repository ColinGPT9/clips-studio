# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ColinGPT9. The Clips Kitty SDK; see sdk/python/LICENSE.
"""The plugin manifest, `clipskitty.yaml`: reading it and checking it.

    from clipskitty_sdk.manifest import load, validate

    report = validate(load("."))
    for problem in report.errors:
        print(problem)            # "settings.min_kills.default: 9 is above the maximum, 6"

`validate` takes the parsed mapping, so a manifest written as JSON works too.
It reports every problem at once, each with the path of the field, and never
evaluates anything in the manifest: it is data. The registry's index build,
the plugin manager and `python -m clipskitty_sdk validate` all use it, so a
manifest that passes here passes there.

Reading YAML needs PyYAML (`pip install pyyaml`); checking a mapping needs
only the standard library. The JSON Schema in schema/clipskitty.schema.json,
for editors, is generated from the tables below (`json_schema()`), so there
is one source of truth.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .contract import STEPS, SUPPORTED_PLUGIN_APIS

MANIFEST_FILE = "clipskitty.yaml"
MANIFEST_VERSIONS = (1,)

# ---- the vocabulary ------------------------------------------------------------

ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,38}/[a-z0-9][a-z0-9-]{0,63}$"
VERSION_PATTERN = r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]{0,39}$"
LABEL_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,39}$"
SETTING_PATTERN = r"^[a-z][a-z0-9_]{0,39}$"
LICENSE_PATTERN = r"^[A-Za-z0-9.+-]+( (AND|OR|WITH) [A-Za-z0-9.+-]+)*$"
HOST_PATTERN = r"^[A-Za-z0-9.-]+(:[0-9]{1,5})?$"
RANGE_PATTERN = r"^\s*(>=|<=|==|!=|>|<|~=)?\s*\d+(\.\d+){0,2}\s*(,\s*(>=|<=|==|!=|>|<|~=)?\s*\d+(\.\d+){0,2}\s*)*$"
HF_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$"
OLLAMA_ID_PATTERN = r"^[a-z0-9][a-z0-9._/-]*(:[A-Za-z0-9._-]+)?$"
HEX40_PATTERN = r"^[0-9a-f]{40}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"

ID_RE = re.compile(ID_PATTERN)
VERSION_RE = re.compile(VERSION_PATTERN)

RESERVED_PUBLISHERS = ("clipskitty",)

# Built in plugin API 1, and named-but-planned values: a planned value gets a
# message saying so rather than "unknown".
KINDS = ("pipeline",)
PLANNED_KINDS = ("caption-style", "publisher", "source", "integration", "provider", "component")
CAPABILITIES = ("highlight_detection",)
EXECUTIONS = ("local", "remote", "hybrid")
INPUTS = ("video", "transcript", "moments")
OUTPUTS = ("ranges", "ratings", "context", "edits")
PLANNED_OUTPUTS = ("clips",)
PERMISSIONS = ("video.read", "transcript.read", "ffmpeg", "ollama", "gpu", "network",
               "filesystem.read", "filesystem.write", "project.read", "project.write")
PLANNED_PERMISSIONS = ("clips.write",)
SENDS_DATA = ("video", "video_link", "audio", "frames", "transcript")
CATEGORIES = ("gaming", "sports", "creators", "streaming", "podcasting", "captions", "detection",
              "analytics", "audio", "utilities")
SETTING_TYPES = ("string", "integer", "number", "boolean", "choice", "secret")
MODEL_SOURCES = ("huggingface", "ollama", "url", "bundled")
GPU_NEEDS = ("none", "optional", "recommended", "required")
OPERATING_SYSTEMS = ("windows", "macos", "linux")
# Files that can run code when a library loads them (pickle).
PICKLE_SUFFIXES = (".bin", ".pt", ".pth", ".ckpt", ".pkl", ".pickle")
MAX_TAGS = 10
MAX_TIMEOUT_MINUTES = 24 * 60

REQUIRED = ("manifest_version", "id", "name", "version", "kind", "capability", "description", "license",
            "requires", "run", "execution", "inputs", "outputs", "permissions")
OPTIONAL = ("author", "repository", "events", "games", "settings", "models", "network", "sends",
            "requirements", "category", "tags", "links", "service", "examples", "based_on")
# How a plugin builds on someone else's project (based_on[].how).
BASED_ON_HOW = ("runs", "includes-code", "port")
MAX_BASED_ON = 10
# The permission each input needs. Moments need none: what was said in them
# (their title, reason and notes) comes only with transcript.read.
INPUT_NEEDS = {"video": "video.read", "transcript": "transcript.read", "moments": None}


class ManifestError(ValueError):
    """A manifest that can't be read or isn't valid. `errors` lists every problem found."""

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


@dataclass
class Report:
    """What validate() found. A manifest is valid when `errors` is empty;
    `warnings` are worth reading but do not stop an install. `hints` maps the
    path of an unknown field to the known field it is probably a misspelling
    of ("permisions": "permissions"), for `python -m clipskitty_sdk validate`."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    hints: dict[str, str] = field(default_factory=dict, compare=False)

    @property
    def ok(self) -> bool:
        return not self.errors


# ---- reading ---------------------------------------------------------------------


def has_yaml() -> bool:
    """Whether PyYAML, which reading clipskitty.yaml needs, can be imported."""
    try:
        import yaml  # noqa: F401
    except ImportError:
        return False
    return True


def line_marks(text: str) -> dict[str, int]:
    """Where each field of a manifest's text is: its line (from 1), by the
    path validate() names it with ("settings.min_kills.default", "outputs[1]").
    A mapping key's line is the key's; a list item's is the item's. Empty when
    PyYAML is missing or the text isn't valid YAML. Only reads the text's
    structure (yaml.compose): nothing in it is built or run."""
    try:
        import yaml
    except ImportError:
        return {}
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        return {}
    marks: dict[str, int] = {}

    def walk(node, path: str) -> None:
        if isinstance(node, yaml.MappingNode):
            for key, value in node.value:
                if not isinstance(key, yaml.ScalarNode):
                    continue
                where = f"{path}.{key.value}" if path else str(key.value)
                marks.setdefault(where, key.start_mark.line + 1)
                walk(value, where)
        elif isinstance(node, yaml.SequenceNode):
            for i, item in enumerate(node.value):
                where = f"{path}[{i}]"
                marks.setdefault(where, item.start_mark.line + 1)
                walk(item, where)

    walk(root, "")
    return marks


def yaml_data(text: str):
    """What a YAML text holds, read with PyYAML's safe loader (nothing in it
    is built or run), for the SDK's other YAML files, such as a catalog
    listing (clipskitty_sdk.listing). The SDK imports PyYAML only here.
    Raises ImportError without PyYAML, and ValueError for text that isn't
    valid YAML."""
    import yaml

    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError(f"not valid YAML ({e})") from e


def load(folder: str | Path) -> dict:
    """The manifest in a plugin folder, as a mapping. Raises ManifestError."""
    path = Path(folder) / MANIFEST_FILE
    if not path.is_file():
        raise ManifestError([f"no {MANIFEST_FILE} in {folder}"])
    text = path.read_text(encoding="utf-8")
    try:
        import yaml
    except ImportError as e:
        raise ManifestError(["reading clipskitty.yaml needs PyYAML: pip install pyyaml"]) from e
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ManifestError([f"{MANIFEST_FILE} is not valid YAML ({e})"]) from e
    if not isinstance(data, dict):
        raise ManifestError([f"{MANIFEST_FILE} must be a mapping of fields"])
    return data


# ---- checking ------------------------------------------------------------------


class _Check:
    def __init__(self):
        self.report = Report()

    def error(self, where: str, message: str) -> None:
        self.report.errors.append(f"{where}: {message}" if where else message)

    def warn(self, where: str, message: str) -> None:
        self.report.warnings.append(f"{where}: {message}" if where else message)

    # small typed readers: each reports a wrong type and returns None

    def text(self, where: str, value, *, pattern: str | None = None, hint: str = "", limit: int = 500):
        if not isinstance(value, str) or not value.strip():
            self.error(where, "must be text")
            return None
        if len(value) > limit:
            self.error(where, f"is longer than {limit} characters")
        if pattern and not re.match(pattern, value):
            self.error(where, hint or f"{value!r} is not in the expected form")
        return value

    def url(self, where: str, value):
        if self.text(where, value, limit=2000) and not re.match(r"^https://[^\s/$.?#][^\s]*$", value):
            self.error(where, f"must be an https:// link, got {value!r}")

    def items(self, where: str, value, *, allow_empty: bool = True):
        if not isinstance(value, list):
            self.error(where, "must be a list")
            return []
        if not allow_empty and not value:
            self.error(where, "must not be empty")
        return value

    def mapping(self, where: str, value):
        if not isinstance(value, dict):
            self.error(where, "must be a mapping of fields")
            return {}
        return value

    def number(self, where: str, value, *, minimum=None, maximum=None, integer=False):
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        if integer:
            ok = ok and float(value).is_integer()
        if not ok:
            self.error(where, "must be a whole number" if integer else "must be a number")
            return None
        if minimum is not None and value < minimum:
            self.error(where, f"must be at least {minimum}")
        if maximum is not None and value > maximum:
            self.error(where, f"must be at most {maximum}")
        return value

    def choice(self, where: str, value, allowed, *, planned=(), what="value"):
        if value in allowed:
            return True
        if value in planned:
            self.error(where, f"{what} {value!r} is planned, not supported by plugin API 1")
        else:
            self.error(where, f"unknown {what} {value!r}; expected one of: {', '.join(allowed)}")
        return False

    def unknown(self, where: str, data: dict, known) -> None:
        for key in data:
            if key not in known:
                path = f"{where}.{key}" if where else str(key)
                self.warn(path, "unknown field, ignored")
                close = difflib.get_close_matches(str(key), sorted(known), n=1, cutoff=0.75)
                if close:
                    self.report.hints[path] = close[0]


def _as_version(number) -> str:
    """A number YAML read where a version was meant, as a version: 0.1 is
    0.1.0 and 2 is 2.0.0; anything else gets the example 1.0.0."""
    parts = repr(number).split(".")
    if len(parts) <= 3 and all(p.isdigit() for p in parts):
        return ".".join(parts + ["0"] * (3 - len(parts)))
    return "1.0.0"


def _relative_inside(path: str) -> bool:
    p = PurePosixPath(path.replace("\\", "/"))
    return bool(path) and not p.is_absolute() and ".." not in p.parts and not re.match(r"^[A-Za-z]:", path)


def _check_requires(c: _Check, value) -> None:
    req = c.mapping("requires", value)
    if not req:
        return
    c.unknown("requires", req, ("clips_kitty", "plugin_api"))
    if "clips_kitty" not in req:
        c.error("requires.clips_kitty", "is required: the Clips Kitty versions it works with, e.g. \">=2.0\"")
    elif c.text("requires.clips_kitty", req["clips_kitty"], limit=100):
        if not re.match(RANGE_PATTERN, req["clips_kitty"]):
            c.error("requires.clips_kitty", f"{req['clips_kitty']!r} is not a version range like \">=2.0, <3\"")
    api = req.get("plugin_api")
    if api is None:
        c.error("requires.plugin_api", "is required: the plugin contract version it was written for (1)")
    elif isinstance(api, bool) or not isinstance(api, int):
        c.error("requires.plugin_api", "must be a whole number")
    elif api not in SUPPORTED_PLUGIN_APIS:
        c.error("requires.plugin_api", f"needs plugin API {api}; this Clips Kitty supports "
                + ", ".join(str(v) for v in SUPPORTED_PLUGIN_APIS))


def _check_run(c: _Check, value, *, builtin: bool) -> None:
    if value == "builtin":
        if not builtin:
            c.error("run", "'builtin' is only for pipelines that ship inside Clips Kitty")
        return
    run = c.mapping("run", value)
    if not run:
        return
    c.unknown("run", run, ("command", "python_requirements", "timeout_minutes"))
    command = run.get("command")
    if command is None:
        c.error("run.command", "is required: the program to start, as a list, e.g. [\"{python}\", \"src/main.py\"]")
    elif not isinstance(command, list) or not command or not all(isinstance(p, str) and p for p in command):
        c.error("run.command", "must be a non-empty list of text, e.g. [\"{python}\", \"src/main.py\"]")
    else:
        first = command[0]
        if first != "{python}" and not (_relative_inside(first) and "/" in first.replace("\\", "/")):
            # A bare name ("bash", "node") is looked up on PATH, so it would not
            # be the plugin's own program.
            c.error("run.command[0]", f"{first!r} must be {{python}} or a path to a program inside the "
                    "plugin's folder, such as bin/detect.exe or ./detect")
        for i, part in enumerate(command[1:], 1):
            if part == "{python}":
                c.error(f"run.command[{i}]", "{python} may only be the first item")
        if first == "{python}" and len(command) > 1 and not _relative_inside(command[1]):
            c.error("run.command[1]", f"{command[1]!r} must be a script inside the plugin's folder")
    req = run.get("python_requirements")
    if req is not None and c.text("run.python_requirements", req, limit=200) and not _relative_inside(req):
        c.error("run.python_requirements", "must be a file inside the plugin's folder")
    if req is not None:
        c.warn("run.python_requirements", "per-plugin Python packages are planned; until then a pipeline runs on "
               "Clips Kitty's own Python with only the standard library and clipskitty_sdk, so these packages "
               "won't be there")
    timeout = run.get("timeout_minutes")
    if timeout is not None:
        c.number("run.timeout_minutes", timeout, minimum=1, maximum=MAX_TIMEOUT_MINUTES)


def _check_setting(c: _Check, name: str, spec) -> None:
    where = f"settings.{name}"
    if not re.match(SETTING_PATTERN, str(name)):
        c.error(where, "a setting's name is lower case letters, digits and _, starting with a letter")
    spec = c.mapping(where, spec)
    if not spec:
        return
    kind = spec.get("type")
    if kind is None:
        c.error(f"{where}.type", "is required: one of " + ", ".join(SETTING_TYPES))
        return
    if not c.choice(f"{where}.type", kind, SETTING_TYPES, what="setting type"):
        return
    known = {"type", "title", "description", "default"}
    if kind in ("integer", "number"):
        known |= {"minimum", "maximum"}
        for bound in ("minimum", "maximum"):
            if bound in spec:
                c.number(f"{where}.{bound}", spec[bound], integer=kind == "integer")
    if kind == "string":
        known |= {"max_length"}
        if "max_length" in spec:
            c.number(f"{where}.max_length", spec["max_length"], minimum=1, maximum=10_000, integer=True)
    if kind == "choice":
        known |= {"options"}
        options = c.items(f"{where}.options", spec.get("options"), allow_empty=False)
        if not all(isinstance(o, str) and o for o in options):
            c.error(f"{where}.options", "must be a list of text")
        elif len(set(options)) != len(options):
            c.error(f"{where}.options", "lists an option twice")
    c.unknown(where, spec, known)
    for key in ("title", "description"):
        if key in spec:
            c.text(f"{where}.{key}", spec[key], limit=200 if key == "title" else 1000)
    if kind == "secret":
        if "default" in spec:
            c.error(f"{where}.default", "a secret has no default: the user enters it")
        return
    if "default" in spec:
        problem = setting_value_problem(spec, spec["default"])
        if problem:
            c.error(f"{where}.default", problem)


def setting_value_problem(spec: dict, value) -> str | None:
    """Why `value` doesn't fit a setting's spec, or None when it does."""
    kind = spec.get("type")
    if kind == "boolean":
        return None if isinstance(value, bool) else f"{value!r} is not true or false"
    if kind in ("integer", "number"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"{value!r} is not a number"
        if kind == "integer" and not float(value).is_integer():
            return f"{value!r} is not a whole number"
        lo, hi = spec.get("minimum"), spec.get("maximum")
        if isinstance(lo, (int, float)) and value < lo:
            return f"{value!r} is below the minimum, {lo}"
        if isinstance(hi, (int, float)) and value > hi:
            return f"{value!r} is above the maximum, {hi}"
        return None
    if kind == "string":
        if not isinstance(value, str):
            return f"{value!r} is not text"
        limit = spec.get("max_length")
        if isinstance(limit, int) and len(value) > limit:
            return f"is longer than {limit} characters"
        return None
    if kind == "choice":
        options = spec.get("options") or []
        return None if value in options else f"{value!r} is not one of: {', '.join(map(str, options))}"
    if kind == "secret":
        return "a secret is set in the plugin's settings, not in a job"
    return f"unknown setting type {kind!r}"


def _check_model(c: _Check, i: int, entry, names: set) -> None:
    where = f"models[{i}]"
    m = c.mapping(where, entry)
    if not m:
        return
    c.unknown(where, m, ("name", "source", "id", "revision", "sha256", "files", "format", "license",
                         "size_bytes", "gated"))
    name = m.get("name")
    if c.text(f"{where}.name", name, pattern=LABEL_PATTERN, hint="lower case letters, digits, _ and -", limit=40):
        if name in names:
            c.error(f"{where}.name", f"{name!r} is used twice")
        names.add(name)
    source = m.get("source")
    if not c.choice(f"{where}.source", source, MODEL_SOURCES, what="model source"):
        return
    mid = m.get("id")
    if source == "huggingface":
        c.text(f"{where}.id", mid, pattern=HF_ID_PATTERN, hint="must look like owner/name")
        rev = m.get("revision")
        if not isinstance(rev, str) or not re.match(HEX40_PATTERN, rev):
            c.error(f"{where}.revision", "must be the full 40-character commit hash, so the files can't change "
                    "under the user (a branch or tag can)")
        files = c.items(f"{where}.files", m.get("files"), allow_empty=False)
        for j, f in enumerate(files):
            if not isinstance(f, str) or not _relative_inside(f):
                c.error(f"{where}.files[{j}]", "must be a file path inside the repository")
            elif f.lower().endswith(PICKLE_SUFFIXES):
                c.warn(f"{where}.files[{j}]", f"{f} is a pickle-format file, which can run code when loaded; "
                       "Clips Kitty will ask the user before downloading it")
    elif source == "ollama":
        c.text(f"{where}.id", mid, pattern=OLLAMA_ID_PATTERN, hint="must look like name:tag")
        rev = m.get("revision")
        if rev is not None and (not isinstance(rev, str) or not re.match(r"^(sha256:)?[0-9a-f]{12,64}$", rev)):
            c.error(f"{where}.revision", "must be the model's digest")
    elif source == "url":
        c.url(f"{where}.id", mid)
        sha = m.get("sha256")
        if not isinstance(sha, str) or not re.match(SHA256_PATTERN, sha):
            c.error(f"{where}.sha256", "is required for a url model: the file's SHA-256, 64 hex characters")
        if isinstance(mid, str) and mid.lower().split("#")[0].split("?")[0].endswith(PICKLE_SUFFIXES):
            c.warn(f"{where}.id", "a pickle-format file, which can run code when loaded; Clips Kitty will ask "
                   "the user before downloading it")
    elif source == "bundled":
        c.text(f"{where}.id", mid, limit=80)
    if "license" in m:
        c.text(f"{where}.license", m["license"], limit=100)
    if "size_bytes" in m:
        c.number(f"{where}.size_bytes", m["size_bytes"], minimum=0, integer=True)


def _check_sends(c: _Check, value) -> list:
    entries = c.items("sends", value)
    for i, entry in enumerate(entries):
        where = f"sends[{i}]"
        if isinstance(entry, str):
            c.choice(where, entry, SENDS_DATA, what="kind of data")
            continue
        e = c.mapping(where, entry)
        if not e:
            continue
        c.unknown(where, e, ("data", "to", "when"))
        c.choice(f"{where}.data", e.get("data"), SENDS_DATA, what="kind of data")
        if "to" not in e:
            c.error(f"{where}.to", "is required: who receives it, in words the user understands")
        else:
            c.text(f"{where}.to", e["to"], limit=200)
        if "when" in e:
            c.text(f"{where}.when", e["when"], limit=200)
    return entries


def _check_requirements(c: _Check, value) -> None:
    req = c.mapping("requirements", value)
    c.unknown("requirements", req, ("gpu", "vram_gb", "ram_gb", "disk_gb", "os", "software"))
    if "gpu" in req:
        c.choice("requirements.gpu", req["gpu"], GPU_NEEDS, what="GPU need")
    for key in ("vram_gb", "ram_gb", "disk_gb"):
        if key in req:
            c.number(f"requirements.{key}", req[key], minimum=0, maximum=1024)
    if "os" in req:
        for i, name in enumerate(c.items("requirements.os", req["os"], allow_empty=False)):
            c.choice(f"requirements.os[{i}]", name, OPERATING_SYSTEMS, what="operating system")
    if "software" in req:
        for i, name in enumerate(c.items("requirements.software", req["software"])):
            c.text(f"requirements.software[{i}]", name, limit=80)


def _words(data, key: str) -> list:
    """A manifest's list field (inputs, outputs), or [] when it isn't a list."""
    value = data.get(key) if isinstance(data, dict) else None
    return value if isinstance(value, list) else []


def _check_steps(c: _Check, data: dict) -> None:
    """The rules that tie the moment words together. Each needs one of the
    words `moments`, `ratings`, `context` or `edits`, and no two can fire on
    one manifest, so a manifest gets at most one of these errors."""
    inputs, outputs = _words(data, "inputs"), _words(data, "outputs")
    if "ratings" in outputs and "moments" not in inputs:
        c.error(f"outputs[{outputs.index('ratings')}]", "ratings score moments found before this plugin runs: "
                "add moments to inputs (a pipeline's own ranges carry their score already)")
    if "moments" in inputs and not any(w in outputs for w in ("ratings", "context", "edits")):
        c.error(f"inputs[{inputs.index('moments')}]", "a plugin given moments answers about them: "
                "add ratings, context or edits to outputs")
    if "edits" in outputs and "moments" not in inputs and "ratings" not in outputs:
        # with ratings it is the first rule's, and its fix fixes both
        c.error(f"outputs[{outputs.index('edits')}]", "edits are suggested for the clips Clips Kitty makes "
                "from moments: add moments to inputs")
    if ("context" in outputs and "ranges" not in outputs and "moments" not in inputs
            and "ratings" not in outputs and "edits" not in outputs):
        # with ratings or edits it is that rule's, and adding moments fixes both
        c.error(f"outputs[{outputs.index('context')}]", "context describes moments: add ranges to outputs, "
                "or moments to inputs")


def validate(data, *, builtin: bool = False) -> Report:
    """Every problem with a manifest mapping. `builtin` is for the manifests
    that ship inside Clips Kitty (plugins/builtin/), the only ones that may use
    the reserved publisher and `run: builtin`."""
    c = _Check()
    if not isinstance(data, dict):
        c.error("", "a manifest is a mapping of fields")
        return c.report
    for key in REQUIRED:
        if key not in data:
            c.error(key, "is required")
    c.unknown("", data, REQUIRED + OPTIONAL)

    mv = data.get("manifest_version")
    if "manifest_version" in data and mv not in MANIFEST_VERSIONS:
        c.error("manifest_version", f"{mv!r} is not a manifest version this Clips Kitty reads "
                f"({', '.join(map(str, MANIFEST_VERSIONS))})")
    pid = data.get("id")
    if "id" in data and c.text("id", pid, pattern=ID_PATTERN, limit=103,
                               hint="must look like publisher/name: lower case letters, digits and hyphens"):
        publisher = pid.split("/", 1)[0]
        if publisher in RESERVED_PUBLISHERS and not builtin:
            c.error("id", f"the publisher {publisher!r} is reserved for plugins that ship with Clips Kitty")
    if "name" in data:
        c.text("name", data["name"], limit=60)
    if "version" in data:
        version = data["version"]
        if isinstance(version, (int, float)) and not isinstance(version, bool):
            # `version: 0.1` unquoted: YAML reads a number, not text.
            c.error("version", f"YAML read this as the number {version!r}; write a version like "
                    f"{_as_version(version)}")
        else:
            c.text("version", version, pattern=VERSION_PATTERN, limit=60,
                   hint=f"{version!r} is not a version like 1.2.0 (SemVer)")
    if "kind" in data:
        c.choice("kind", data["kind"], KINDS, planned=PLANNED_KINDS, what="kind")
    if "capability" in data:
        c.choice("capability", data["capability"], CAPABILITIES, what="capability")
    if "description" in data:
        c.text("description", data["description"], limit=1000)
    if "license" in data:
        c.text("license", data["license"], pattern=LICENSE_PATTERN, limit=100,
               hint="must be an SPDX licence identifier such as MIT or Apache-2.0")
    if "requires" in data:
        _check_requires(c, data["requires"])
    if "run" in data:
        _check_run(c, data["run"], builtin=builtin)
    execution = data.get("execution")
    if "execution" in data:
        c.choice("execution", execution, EXECUTIONS, what="execution")

    permissions = set()
    if "permissions" in data:
        for i, perm in enumerate(c.items("permissions", data["permissions"])):
            if c.choice(f"permissions[{i}]", perm, PERMISSIONS, planned=PLANNED_PERMISSIONS, what="permission"):
                permissions.add(perm)
    if "inputs" in data:
        for i, item in enumerate(c.items("inputs", data["inputs"])):
            if (c.choice(f"inputs[{i}]", item, INPUTS, what="input") and INPUT_NEEDS[item]
                    and INPUT_NEEDS[item] not in permissions):
                c.error(f"inputs[{i}]", f"the {item} input needs the {INPUT_NEEDS[item]} permission")
    if "outputs" in data:
        for i, item in enumerate(c.items("outputs", data["outputs"], allow_empty=False)):
            c.choice(f"outputs[{i}]", item, OUTPUTS, planned=PLANNED_OUTPUTS, what="output")
    _check_steps(c, data)

    network = c.items("network", data.get("network", []))
    for i, host in enumerate(network):
        c.text(f"network[{i}]", host, pattern=HOST_PATTERN, limit=255, hint="must be a host name, e.g. api.example.com")
    sends = _check_sends(c, data.get("sends", []))
    if network and "network" not in permissions:
        c.error("permissions", "lists no network permission, but network names hosts it connects to")
    if "network" in permissions and not network:
        c.error("network", "must name the hosts it connects to, since the plugin asks for the network permission")
    if execution in ("remote", "hybrid"):
        if not network:
            c.error("network", f"a {execution} pipeline must name the hosts it connects to")
        if not sends:
            c.error("sends", f"a {execution} pipeline must say what leaves the computer")
    if execution == "local" and sends:
        c.error("execution", "a local pipeline sends nothing off this computer; with sends listed, "
                "execution must be hybrid or remote")

    for key in ("events", "games"):
        if key in data:
            for i, value in enumerate(c.items(key, data[key])):
                c.text(f"{key}[{i}]", value, pattern=LABEL_PATTERN if key == "events" else SLUG_PATTERN,
                       hint="lower case letters, digits and hyphens", limit=40)
    if "settings" in data:
        for name, spec in c.mapping("settings", data["settings"]).items():
            _check_setting(c, name, spec)
    if "models" in data:
        names: set = set()
        for i, entry in enumerate(c.items("models", data["models"])):
            _check_model(c, i, entry, names)
    if "requirements" in data:
        _check_requirements(c, data["requirements"])
    if "category" in data:
        c.choice("category", data["category"], CATEGORIES, what="category")
    if "tags" in data:
        tags = c.items("tags", data["tags"])
        if len(tags) > MAX_TAGS:
            c.error("tags", f"at most {MAX_TAGS} tags")
        for i, tag in enumerate(tags):
            c.text(f"tags[{i}]", tag, pattern=SLUG_PATTERN, hint="lower case letters, digits and hyphens", limit=40)
    if "repository" in data:
        c.url("repository", data["repository"])
    if "author" in data:
        author = c.mapping("author", data["author"])
        c.unknown("author", author, ("name", "url"))
        if author:
            c.text("author.name", author.get("name"), limit=100)
            if "url" in author:
                c.url("author.url", author["url"])
    if "links" in data:
        links = c.mapping("links", data["links"])
        c.unknown("links", links, ("docs", "funding"))
        if "docs" in links:
            c.url("links.docs", links["docs"])
        for i, link in enumerate(c.items("links.funding", links.get("funding", []))):
            c.url(f"links.funding[{i}]", link)
    if data.get("service") is not None:
        service = c.mapping("service", data["service"])
        c.unknown("service", service, ("name", "url", "pricing", "required"))
        if service:
            c.text("service.name", service.get("name"), limit=100)
            c.url("service.url", service.get("url"))
            if "pricing" in service:
                c.text("service.pricing", service["pricing"], limit=200)
            if not isinstance(service.get("required"), bool):
                c.error("service.required", "must be true or false: whether the plugin works without the service")
    if "examples" in data:
        for i, ex in enumerate(c.items("examples", data["examples"])):
            e = c.mapping(f"examples[{i}]", ex)
            if e:
                c.unknown(f"examples[{i}]", e, ("title", "url"))
                c.text(f"examples[{i}].title", e.get("title"), limit=100)
                c.url(f"examples[{i}].url", e.get("url"))
    if "based_on" in data:
        # Whose work this plugin builds on, so the Marketplace can credit it
        # and show its licence beside the plugin's own.
        items = c.items("based_on", data["based_on"])
        if len(items) > MAX_BASED_ON:
            c.error("based_on", f"at most {MAX_BASED_ON} projects")
        for i, item in enumerate(items):
            b = c.mapping(f"based_on[{i}]", item)
            if b:
                c.unknown(f"based_on[{i}]", b, ("name", "url", "license", "how"))
                c.text(f"based_on[{i}].name", b.get("name"), limit=100)
                c.url(f"based_on[{i}].url", b.get("url"))
                c.text(f"based_on[{i}].license", b.get("license"), pattern=LICENSE_PATTERN, limit=100,
                       hint="the other project's licence as an SPDX identifier, such as MIT")
                # runs: starts it as a separate program; includes-code: contains its code; port: rewrites it.
                c.choice(f"based_on[{i}].how", b.get("how"), BASED_ON_HOW, what="kind of use")
    return c.report


def validate_folder(folder: str | Path, *, builtin: bool = False) -> tuple[dict | None, Report]:
    """Load and validate the manifest in a plugin folder, and check the files
    it names exist there. Returns (manifest or None, report)."""
    folder = Path(folder)
    try:
        data = load(folder)
    except ManifestError as e:
        return None, Report(errors=list(e.errors))
    report = validate(data, builtin=builtin)
    run = data.get("run")
    if isinstance(run, dict):
        command = run.get("command")
        if isinstance(command, list) and command and all(isinstance(p, str) for p in command):
            target = command[1] if command[0] == "{python}" and len(command) > 1 else command[0]
            if target != "{python}" and _relative_inside(target) and not (folder / target).is_file():
                report.errors.append(f"run.command: {target} is not in the plugin's folder")
        req = run.get("python_requirements")
        if isinstance(req, str) and _relative_inside(req) and not (folder / req).is_file():
            report.errors.append(f"run.python_requirements: {req} is not in the plugin's folder")
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            report.errors.append(f"{path.relative_to(folder).as_posix()}: symbolic links are not allowed in a plugin")
    return data, report


# ---- what a plugin does: find, understand, rate, edit -------------------------------
#
# A plugin's role follows from its inputs and outputs; there is no field for it.
# These functions decide what a plugin does and what a job may ask of it. Read
# a plugin's role through them, not from its inputs and outputs directly, so
# every place that asks gets the same answer.

# The output that does each step.
STEP_OUTPUTS = {"find": "ranges", "understand": "context", "rate": "ratings", "edit": "edits"}


def steps_of(manifest) -> tuple[str, ...]:
    """What a plugin does, in run order: find for `ranges`, understand for
    `context`, rate for `ratings`, edit for `edits`. A finder that declares
    `context` describes its own ranges: it does find and understand, but is
    offered only find."""
    outputs = _words(manifest, "outputs")
    return tuple(step for step in STEPS if STEP_OUTPUTS[step] in outputs)


def offers(manifest) -> tuple[str, ...]:
    """The steps a job may name a plugin for: find for `ranges`; understand,
    rate and edit for `context`, `ratings` and `edits` when it also takes
    `moments` in."""
    given = "moments" in _words(manifest, "inputs")
    return tuple(step for step in steps_of(manifest) if step == "find" or given)


def find_steps(manifest) -> tuple[str, ...]:
    """What a find run asks the plugin for: find, and understand too when it
    describes the ranges it finds (outputs `ranges` and `context`)."""
    outputs = _words(manifest, "outputs")
    return ("find", "understand") if "ranges" in outputs and "context" in outputs else ("find",)


def uses_steps(manifest) -> bool:
    """Whether a manifest uses any of the words `moments`, `ratings`,
    `context` or `edits`. Only then does a find run's job.json carry `steps`,
    so a plain finder's job is exactly what it always was."""
    words = _words(manifest, "inputs") + _words(manifest, "outputs")
    return any(w in words for w in ("moments", "ratings", "context", "edits"))


def step_problem(manifest, step: str) -> str | None:
    """Why a job can't name this plugin for `step`, or None when it can. The
    text follows "the pipeline {name} " in the app's messages."""
    if step not in STEPS:
        raise ValueError(f"unknown step {step!r}; expected one of: {', '.join(STEPS)}")
    if step in offers(manifest):
        return None
    if step == "find":
        if steps_of(manifest) == ("edit",):
            return ("doesn't find moments: it suggests edits for the clips Clips Kitty makes. "
                    "Choose it under Suggest edits instead")
        return ("doesn't find moments: it rates or understands moments others found. "
                "Choose it under Rate & understand instead")
    if step == "edit":
        return "can't suggest edits for clips: its manifest needs moments in inputs and edits in outputs"
    return (f"can't {step} moments others found: its manifest needs moments in inputs "
            f"and {STEP_OUTPUTS[step]} in outputs")


# ---- version ranges ----------------------------------------------------------------


def _version_tuple(text: str) -> tuple[int, int, int]:
    core = re.split(r"[-+]", text.strip(), maxsplit=1)[0]
    parts = [int(p) for p in core.split(".")[:3]]
    return tuple(parts + [0] * (3 - len(parts)))  # type: ignore[return-value]


def version_key(text: str) -> tuple:
    """A sort key in Semantic Versioning order: 1.2.0-rc.1 < 1.2.0 < 1.2.1.
    A pre-release sorts below its release; its dot-separated parts compare
    as numbers when they are numbers (below any word), else as text; build
    metadata (+...) is ignored."""
    text = text.strip().split("+", 1)[0]
    core, _, pre = text.partition("-")
    parts = pre.split(".") if pre else []
    return (_version_tuple(core), 0 if parts else 1,
            tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in parts))


def version_satisfies(version: str, spec: str) -> bool:
    """Whether a version (2.0.0) is inside a range (">=2.0, <3"). `~=2.1`
    means >=2.1 and <3; `~=2.1.0` means >=2.1.0 and <2.2."""
    have = _version_tuple(version)
    for part in spec.split(","):
        m = re.match(r"^\s*(>=|<=|==|!=|>|<|~=)?\s*(\d+(?:\.\d+){0,2})\s*$", part)
        if not m:
            raise ValueError(f"not a version range: {spec!r}")
        op, text = m.group(1) or "==", m.group(2)
        want = _version_tuple(text)
        if op == "~=":
            depth = len(text.split("."))
            upper = list(want[: max(1, depth - 1)])
            upper[-1] += 1
            ok = want <= have < tuple(upper + [0] * (3 - len(upper)))
        else:
            ok = {">=": have >= want, "<=": have <= want, ">": have > want, "<": have < want,
                  "==": have == want, "!=": have != want}[op]
        if not ok:
            return False
    return True


# ---- the JSON Schema, generated from the tables above ----------------------------------


def json_schema() -> dict:
    """A JSON Schema (draft 2020-12) for editors, made from this module's tables.
    It checks shapes, names and vocabularies; rules that span fields (an input
    needs its permission, remote needs sends, ratings need moments) are only
    in validate()."""
    text = {"type": "string", "minLength": 1}
    https = {"type": "string", "pattern": r"^https://"}

    def enum(values):
        return {"enum": list(values)}

    def slugs(pattern):
        return {"type": "array", "items": {"type": "string", "pattern": pattern}}

    setting = {
        "type": "object", "required": ["type"],
        "properties": {"type": enum(SETTING_TYPES), "title": text, "description": text, "default": {},
                       "minimum": {"type": "number"}, "maximum": {"type": "number"},
                       "max_length": {"type": "integer", "minimum": 1},
                       "options": {"type": "array", "items": text, "minItems": 1}},
    }
    model = {
        "type": "object", "required": ["name", "source", "id"],
        "properties": {"name": {"type": "string", "pattern": LABEL_PATTERN}, "source": enum(MODEL_SOURCES),
                       "id": text, "revision": text, "sha256": {"type": "string", "pattern": SHA256_PATTERN},
                       "files": {"type": "array", "items": text}, "format": text, "license": text,
                       "size_bytes": {"type": "integer", "minimum": 0}, "gated": {"type": "boolean"}},
    }
    sends_entry = {"oneOf": [
        enum(SENDS_DATA),
        {"type": "object", "required": ["data", "to"],
         "properties": {"data": enum(SENDS_DATA), "to": text, "when": text}},
    ]}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Clips Kitty plugin manifest (clipskitty.yaml)",
        "description": "Generated by clipskitty_sdk.manifest.json_schema(); "
                       "python -m clipskitty_sdk validate checks more than this schema can.",
        "type": "object",
        "required": list(REQUIRED),
        "properties": {
            "manifest_version": enum(MANIFEST_VERSIONS),
            "id": {"type": "string", "pattern": ID_PATTERN},
            "name": text,
            "version": {"type": "string", "pattern": VERSION_PATTERN},
            "kind": enum(KINDS),
            "capability": enum(CAPABILITIES),
            "description": text,
            "author": {"type": "object", "required": ["name"], "properties": {"name": text, "url": https}},
            "repository": https,
            "license": {"type": "string", "pattern": LICENSE_PATTERN},
            "requires": {"type": "object", "required": ["clips_kitty", "plugin_api"],
                         "properties": {"clips_kitty": {"type": "string", "pattern": RANGE_PATTERN},
                                        "plugin_api": enum(SUPPORTED_PLUGIN_APIS)}},
            "run": {"type": "object", "required": ["command"],
                    "properties": {"command": {"type": "array", "items": text, "minItems": 1},
                                   "python_requirements": text,
                                   "timeout_minutes": {"type": "number", "minimum": 1,
                                                       "maximum": MAX_TIMEOUT_MINUTES}}},
            "execution": enum(EXECUTIONS),
            "inputs": {"type": "array", "items": enum(INPUTS)},
            "outputs": {"type": "array", "items": enum(OUTPUTS), "minItems": 1},
            "events": slugs(LABEL_PATTERN),
            "games": slugs(SLUG_PATTERN),
            "settings": {"type": "object", "propertyNames": {"pattern": SETTING_PATTERN},
                         "additionalProperties": setting},
            "models": {"type": "array", "items": model},
            "permissions": {"type": "array", "items": enum(PERMISSIONS)},
            "network": slugs(HOST_PATTERN),
            "sends": {"type": "array", "items": sends_entry},
            "requirements": {"type": "object", "properties": {
                "gpu": enum(GPU_NEEDS), "vram_gb": {"type": "number", "minimum": 0},
                "ram_gb": {"type": "number", "minimum": 0}, "disk_gb": {"type": "number", "minimum": 0},
                "os": {"type": "array", "items": enum(OPERATING_SYSTEMS)},
                "software": {"type": "array", "items": text}}},
            "category": enum(CATEGORIES),
            "tags": {"type": "array", "items": {"type": "string", "pattern": SLUG_PATTERN}, "maxItems": MAX_TAGS},
            "links": {"type": "object", "properties": {"docs": https, "funding": {"type": "array", "items": https}}},
            "service": {"oneOf": [{"type": "null"}, {
                "type": "object", "required": ["name", "url", "required"],
                "properties": {"name": text, "url": https, "pricing": text, "required": {"type": "boolean"}}}]},
            "examples": {"type": "array", "items": {"type": "object", "required": ["title", "url"],
                                                    "properties": {"title": text, "url": https}}},
            "based_on": {"type": "array", "maxItems": MAX_BASED_ON, "items": {
                "type": "object", "required": ["name", "url", "license", "how"],
                "properties": {"name": text, "url": https, "license": {"type": "string", "pattern": LICENSE_PATTERN},
                               "how": enum(BASED_ON_HOW)}}},
        },
    }


SCHEMA_FILE = Path(__file__).resolve().parent / "schema" / "clipskitty.schema.json"


def schema_text() -> str:
    return json.dumps(json_schema(), indent=2, ensure_ascii=False) + "\n"
