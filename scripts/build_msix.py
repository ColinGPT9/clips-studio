"""Build the Microsoft Store package.

    python scripts/build_msix.py

The sibling of build_installer.py. That script produces the NSIS installer
that ships from GitHub and Hugging Face; this one produces the MSIX/appx
package that goes to Partner Center. They build the same application from the
same source: only the wrapper and two runtime behaviours differ, and those are
decided at runtime in ui/src/main/distribution.ts rather than by compiling
something different.

    1. check the build tools, and Developer Mode
    2. freeze the Python engine (or reuse it with --skip-backend)
    3. build the Electron front end
    4. wrap both in an appx package -> release/

Flags:
    --skip-backend    reuse the frozen backend from a previous run
    --skip-ui         reuse the previously built renderer

WHAT YOU MUST DO FIRST
    Fill in the four placeholders in the `appx:` block of
    ui/electron-builder.yml with the values Partner Center gives you. This
    script refuses to build without them, because a package built on
    placeholder identity cannot be uploaded and finding that out at the upload
    step wastes an hour of packaging.

    docs/MSSTORE.md walks through getting them.
"""

import argparse
import json
import re
import sys
import winreg
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
BUILDER_CONFIG = UI / "electron-builder.yml"
PACKAGE_JSON = UI / "package.json"
RELEASE = ROOT / "release"

sys.path.insert(0, str(ROOT / "scripts"))
from build_installer import (  # noqa: E402
    build_ui,
    check_tools,
    ensure_vendored,
    freeze_backend,
    run,
    say,
    smoke_test_backend,
)


def store_version(app_version: str) -> str:
    """The four-part package version for an app version like "0.1.2".

    Microsoft's rule, from the MSIX app package requirements: the fourth part
    is reserved for the Store and must be 0, the rest are 0-65535, "except for
    the first section, which cannot be 0". So 0.1.2.0 -- the obvious choice --
    is rejected at upload.

    The mapping adds one to the major, and nothing else:

        0.1.2  ->  1.1.2.0
        0.1.3  ->  1.1.3.0
        0.2.0  ->  1.2.0.0
        1.0.0  ->  2.0.0.0

    It has to be that rather than a fixed leading 1, because the Store also
    requires each submission to be higher than the last. Pinning the major at 1
    works until the app reaches 1.0.0, at which point 1.0.0.0 sorts BELOW the
    1.1.2.0 already published and the update is refused. Deriving the major
    from the app's own keeps it monotonic through that boundary.

    The user-facing version stays whatever package.json says. Only the package
    identity uses this.
    """
    parts = app_version.split("-")[0].split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        sys.exit(f"\nCannot map version {app_version!r} to a Store version: expected X.Y.Z")
    major, minor, patch = (int(p) for p in parts)
    for name, value in (("minor", minor), ("patch", patch)):
        if value > 65535:
            sys.exit(f"\n{name} version {value} exceeds the 65535 the Store allows")
    if major + 1 > 65535:
        sys.exit(f"\nmajor version {major} is too large to map")
    return f"{major + 1}.{minor}.{patch}.0"


def developer_mode_on() -> bool:
    """Windows Developer Mode, which this build needs for two reasons.

    electron-builder's appx target runs makeappx.exe out of its winCodeSign
    download, and that archive carries macOS symlinks an ordinary account
    cannot extract -- the same failure that put `signAndEditExecutable: false`
    in electron-builder.yml. Installing the finished package for testing needs
    it too, because the package is deliberately unsigned.
    """
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock",
        )
        with key:
            return winreg.QueryValueEx(key, "AllowDevelopmentWithoutDevLicense")[0] == 1
    except OSError:
        return False


def read_app_version() -> str:
    return json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))["version"]


def check_identity() -> None:
    """Refuse to build on placeholder Store identity."""
    text = BUILDER_CONFIG.read_text(encoding="utf-8")
    block = re.search(r"^appx:\n(?:[ \t].*\n|\n)*", text, re.MULTILINE)
    if not block:
        sys.exit(f"\nNo `appx:` block in {BUILDER_CONFIG.relative_to(ROOT)}.")
    missing = [
        field
        for field in ("identityName", "publisher", "publisherDisplayName")
        if re.search(rf"^\s+{field}:\s*PLACEHOLDER", block.group(0), re.MULTILINE)
    ]
    if missing:
        sys.exit(
            "\nStore identity is still on placeholders: "
            + ", ".join(missing)
            + f"\n\nFill them in in {BUILDER_CONFIG.relative_to(ROOT)} with the values from\n"
            "Partner Center -> General -> View product identity.\n"
            "docs/MSSTORE.md walks through it.\n\n"
            "A package built on placeholders cannot be uploaded, so this stops here\n"
            "rather than after twenty minutes of packaging."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-backend", action="store_true")
    parser.add_argument("--skip-ui", action="store_true")
    args = parser.parse_args()

    if sys.platform != "win32":
        sys.exit("The Store package can only be built on Windows.")

    app_version = read_app_version()
    package_version = store_version(app_version)

    say("1/4", "checking build tools and Store identity")
    check_tools(args.skip_ui)
    check_identity()
    if not developer_mode_on():
        sys.exit(
            "\nWindows Developer Mode is off, and the appx build needs it.\n\n"
            "  Settings > System > For developers > Developer Mode\n\n"
            "electron-builder unpacks makeappx.exe from an archive containing macOS\n"
            "symlinks, which an ordinary account cannot extract. Developer Mode is\n"
            "also what lets you install the finished package to test it."
        )
    print("    Developer Mode: on")
    print(f"    app version:     {app_version}")
    print(f"    package version: {package_version}   (major +1: the Store forbids a 0 major)")

    if not args.skip_backend:
        say("2/4", "fetching vendored binaries and freezing the Python engine")
        ensure_vendored()
        freeze_backend()
        smoke_test_backend()
    else:
        print("\n=== 2/4: reusing the frozen backend")

    if not args.skip_ui:
        build_ui()
    else:
        print("\n=== 3/4: reusing the previously built renderer")

    say("4/4", "packaging for the Microsoft Store")
    # extraMetadata.version is what electron-builder reads to compute the
    # package version, so the Store's numbering is applied here rather than by
    # editing package.json -- the repo keeps saying 0.1.2.
    run(
        [
            "npx",
            "electron-builder",
            "--win",
            "appx",
            "--x64",
            "-c.extraMetadata.version=" + ".".join(package_version.split(".")[:3]),
        ],
        UI,
        "appx packaging",
    )

    packages = sorted(RELEASE.glob("*.appx"), key=lambda p: p.stat().st_mtime, reverse=True)
    print("\nDone.")
    if packages:
        built = packages[0]
        print(f"    {built.relative_to(ROOT)}  ({built.stat().st_size / 1e9:.2f} GB)")
        print(f"\n    Package version {package_version}, app version {app_version}.")
        print("    Upload this file on the Packages page in Partner Center.")
        print("    To test it first, see 'Installing it yourself' in docs/MSSTORE.md.")
    else:
        print("    No .appx found in release/ — check the electron-builder output above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
