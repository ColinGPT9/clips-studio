"""The plugin manifest (clipskitty.yaml): the validator, its schema, and the
manifests in this repository.

The fixtures are the specification. Every file in
tests/fixtures/plugins/manifests/invalid/ breaks one rule and names, in
`# expect:` lines, the messages it must produce, and nothing else may be
reported for it; every file in valid/ must pass. Standard library and PyYAML
only, so these run in CI.
"""

import json
import os
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parent.parent
SDK = ROOT / "sdk" / "python"
if str(SDK) not in sys.path:
    sys.path.insert(0, str(SDK))

from clipskitty_sdk import manifest as mf  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "plugins" / "manifests"
VALID = sorted((FIXTURES / "valid").glob("*.yaml"))
INVALID = sorted((FIXTURES / "invalid").glob("*.yaml"))


def _expects(path: Path) -> list[str]:
    return [line[len("# expect:"):].strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith("# expect:")]


# ---- the fixtures ----------------------------------------------------------------


@pytest.mark.parametrize("path", VALID, ids=[p.stem for p in VALID])
def test_a_valid_manifest_passes(path):
    report = mf.validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    assert report.ok, report.errors


@pytest.mark.parametrize("path", INVALID, ids=[p.stem for p in INVALID])
def test_an_invalid_manifest_says_exactly_what_is_wrong(path):
    expected = _expects(path)
    assert expected, f"{path.name} names no `# expect:` message"
    report = mf.validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    for fragment in expected:
        assert any(fragment in e for e in report.errors), (fragment, report.errors)
    stray = [e for e in report.errors if not any(f in e for f in expected)]
    assert not stray, f"{path.name} also fails for reasons it doesn't name: {stray}"


def test_every_rule_has_a_fixture():
    """Most of the validator's messages appear in some invalid fixture, so a
    rule can't be dropped without a test noticing."""
    names = {p.stem for p in INVALID}
    for needed in ("planned-kind", "input-without-permission", "remote-without-sends", "local-that-sends",
                   "network-without-permission", "model-on-a-branch", "reserved-publisher", "shell-command",
                   "script-outside-folder", "default-above-maximum", "secret-with-default", "newer-plugin-api"):
        assert needed in names


def test_problems_are_all_reported_at_once():
    report = mf.validate({"manifest_version": 1, "id": "Bad Id", "version": "one"})
    assert len(report.errors) >= 10  # every missing field, plus the bad id and version
    assert "id: must look like publisher/name: lower case letters, digits and hyphens" in report.errors


def test_a_manifest_must_be_a_mapping():
    assert mf.validate(["not", "a", "mapping"]).errors == ["a manifest is a mapping of fields"]


def test_unknown_fields_warn_without_failing():
    data = yaml.safe_load((FIXTURES / "valid" / "minimal.yaml").read_text(encoding="utf-8"))
    data["colour"] = "red"
    data["run"]["shell"] = True
    report = mf.validate(data)
    assert report.ok
    assert report.warnings == ["colour: unknown field, ignored", "run.shell: unknown field, ignored"]


def test_pickle_model_files_are_flagged():
    data = yaml.safe_load((FIXTURES / "valid" / "marvel-rivals-design-example.yaml").read_text(encoding="utf-8"))
    data["models"][0]["files"] = ["killfeed.pt"]
    report = mf.validate(data)
    assert report.ok and any("pickle-format" in w for w in report.warnings)


def test_json_works_as_well_as_yaml():
    data = yaml.safe_load((FIXTURES / "valid" / "every-setting-type.yaml").read_text(encoding="utf-8"))
    assert mf.validate(json.loads(json.dumps(data))).ok


# ---- the manifests in this repository -----------------------------------------------


@pytest.mark.parametrize("name", ["shorts", "gaming", "sports"])
def test_the_built_in_modes_have_valid_manifests(name):
    data = yaml.safe_load((ROOT / "plugins" / "builtin" / f"{name}.yaml").read_text(encoding="utf-8"))
    assert mf.validate(data, builtin=True).ok
    assert data["id"] == f"clipskitty/{name}" and data["run"] == "builtin"
    # Only the app's own bundle may use the reserved publisher and `run: builtin`.
    assert {e.split(":")[0] for e in mf.validate(data).errors} == {"id", "run"}


def test_the_built_in_versions_follow_the_app():
    app = json.loads((ROOT / "ui" / "package.json").read_text(encoding="utf-8"))["version"]
    for name in ("shorts", "gaming", "sports"):
        data = yaml.safe_load((ROOT / "plugins" / "builtin" / f"{name}.yaml").read_text(encoding="utf-8"))
        assert data["version"] == app, f"plugins/builtin/{name}.yaml says {data['version']}, the app is {app}"
        assert mf.version_satisfies(app, data["requires"]["clips_kitty"])


@pytest.mark.parametrize("folder", ["tests/fixtures/plugins/echo", "examples/pipelines/transcript-highlights"])
def test_the_plugins_in_this_repository_validate(folder):
    data, report = mf.validate_folder(ROOT / folder)
    assert report.ok, report.errors
    assert not report.warnings, report.warnings


# ---- a plugin folder ----------------------------------------------------------------


def _plugin(tmp_path, **changes) -> Path:
    data = yaml.safe_load((FIXTURES / "valid" / "minimal.yaml").read_text(encoding="utf-8"))
    data.update(changes)
    (tmp_path / "clipskitty.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    return tmp_path


def test_a_folder_with_its_script_validates(tmp_path):
    data, report = mf.validate_folder(_plugin(tmp_path))
    assert report.ok and data["id"] == "example-dev/loud-moments"


def test_the_files_a_manifest_names_must_be_in_the_folder(tmp_path):
    folder = _plugin(tmp_path, run={"command": ["{python}", "src/missing.py"], "python_requirements": "req.txt"})
    _, report = mf.validate_folder(folder)
    assert "run.command: src/missing.py is not in the plugin's folder" in report.errors
    assert "run.python_requirements: req.txt is not in the plugin's folder" in report.errors


@pytest.mark.skipif(os.name == "nt", reason="creating symbolic links needs a privilege on Windows")
def test_a_symbolic_link_in_a_plugin_is_refused(tmp_path):
    folder = _plugin(tmp_path)
    (folder / "src" / "secrets").symlink_to(Path.home())
    _, report = mf.validate_folder(folder)
    assert "src/secrets: symbolic links are not allowed in a plugin" in report.errors


def test_an_unreadable_manifest_is_one_clear_error(tmp_path):
    _, report = mf.validate_folder(tmp_path)
    assert report.errors == [f"no clipskitty.yaml in {tmp_path}"]
    (tmp_path / "clipskitty.yaml").write_text("id: [unclosed", encoding="utf-8")
    _, report = mf.validate_folder(tmp_path)
    assert len(report.errors) == 1 and report.errors[0].startswith("clipskitty.yaml is not valid YAML")
    (tmp_path / "clipskitty.yaml").write_text("- a list\n", encoding="utf-8")
    assert mf.validate_folder(tmp_path)[1].errors == ["clipskitty.yaml must be a mapping of fields"]


# ---- setting values -------------------------------------------------------------------


@pytest.mark.parametrize("spec, value, problem", [
    ({"type": "integer", "minimum": 1, "maximum": 5}, 3, None),
    ({"type": "integer", "minimum": 1, "maximum": 5}, 7, "7 is above the maximum, 5"),
    ({"type": "integer"}, 2.5, "2.5 is not a whole number"),
    ({"type": "integer"}, True, "True is not a number"),
    ({"type": "number", "minimum": 0}, -0.5, "-0.5 is below the minimum, 0"),
    ({"type": "boolean"}, "yes", "'yes' is not true or false"),
    ({"type": "string", "max_length": 3}, "abcd", "is longer than 3 characters"),
    ({"type": "choice", "options": ["a", "b"]}, "c", "'c' is not one of: a, b"),
    ({"type": "secret"}, "abc", "a secret is set in the plugin's settings, not in a job"),
])
def test_setting_values_are_checked_against_their_type(spec, value, problem):
    assert mf.setting_value_problem(spec, value) == problem


# ---- version ranges --------------------------------------------------------------------


@pytest.mark.parametrize("version, spec, ok", [
    ("2.0.0", ">=2.0", True),
    ("1.9.9", ">=2.0", False),
    ("2.5.1", ">=2.0, <3", True),
    ("3.0.0", ">=2.0, <3", False),
    ("2.1.4", "~=2.1", True),
    ("3.0.0", "~=2.1", False),
    ("2.1.9", "~=2.1.0", True),
    ("2.2.0", "~=2.1.0", False),
    ("2.0.0-beta.1", ">=2.0", True),
    ("2.0.0", "==2.0.0", True),
    ("2.0.1", "!=2.0.1", False),
])
def test_version_ranges(version, spec, ok):
    assert mf.version_satisfies(version, spec) is ok


def test_a_range_that_is_not_one_is_refused():
    with pytest.raises(ValueError):
        mf.version_satisfies("2.0.0", "latest")


# ---- the JSON Schema ----------------------------------------------------------------------


def test_the_committed_schema_is_the_generated_one():
    assert mf.SCHEMA_FILE.read_text(encoding="utf-8") == mf.schema_text(), (
        "sdk/python/clipskitty_sdk/schema/clipskitty.schema.json is stale: "
        "run python -m clipskitty_sdk schema --write (with sdk/python on PYTHONPATH)"
    )


def test_the_schema_uses_the_validators_vocabulary():
    schema = mf.json_schema()
    props = schema["properties"]
    assert schema["required"] == list(mf.REQUIRED)
    assert set(props) == set(mf.REQUIRED) | set(mf.OPTIONAL)
    assert props["kind"]["enum"] == list(mf.KINDS)
    assert props["permissions"]["items"]["enum"] == list(mf.PERMISSIONS)
    assert props["category"]["enum"] == list(mf.CATEGORIES)


def test_the_schema_accepts_the_valid_fixtures():
    jsonschema = pytest.importorskip("jsonschema")
    for path in VALID:
        jsonschema.validate(yaml.safe_load(path.read_text(encoding="utf-8")), mf.json_schema())


# ---- python -m clipskitty_sdk validate ----------------------------------------------------


def test_the_validate_command(tmp_path, capsys):
    from clipskitty_sdk.__main__ import main

    assert main(["validate", str(_plugin(tmp_path))]) == 0
    assert "example-dev/loud-moments 1.0.0: valid" in capsys.readouterr().out
    _plugin(tmp_path, kind="publisher")
    assert main(["validate", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "error: kind: kind 'publisher' is planned" in out and "would refuse to install" in out


def test_run_refuses_a_plugin_the_app_would_refuse(tmp_path, capsys):
    from clipskitty_sdk.__main__ import main

    folder = _plugin(tmp_path, permissions=[])
    (tmp_path / "clip.mp4").write_bytes(b"x")
    assert main(["run", str(folder), "--video", str(tmp_path / "clip.mp4")]) == 2
    assert "needs the video.read permission" in capsys.readouterr().err
