"""Sign the Store package with a throwaway certificate, so it can be installed.

    python scripts/sign_msix.py

WHY THIS EXISTS
    The package that goes to Partner Center is deliberately unsigned: Microsoft
    re-signs it with their own certificate after certification, which is why no
    code-signing certificate has to be bought for the Store.

    But Windows will not install an unsigned package, so testing the thing you
    are about to ship means signing it yourself first. That signature is for
    this machine only and has nothing to do with the Store submission -- upload
    the UNSIGNED package from release/, not the one this produces.

WHAT IT NEEDS
    Nothing that is not already here. signtool.exe, makecert.exe and pvk2pfx.exe
    all ship inside electron-builder's winCodeSign download, so the Windows SDK
    is not required. Developer Mode must be on, which it must be to have built
    the package at all.

THE ONE RULE
    The certificate subject must equal the `publisher` value in
    ui/electron-builder.yml EXACTLY. Windows refuses the install otherwise, with
    an error that names neither value.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
RELEASE = ROOT / "release"
BUILDER_CONFIG = ROOT / "ui" / "electron-builder.yml"
WORK = ROOT / "build" / "msix-signing"


def find_tool(name: str) -> Path:
    """A tool from electron-builder's winCodeSign cache.

    Newest first: several extractions accumulate there, one per build that
    failed before Developer Mode was on, and they are all the same version.
    """
    cache = Path(os.environ.get("LOCALAPPDATA", "")) / "electron-builder" / "Cache" / "winCodeSign"
    found = sorted(cache.glob(f"*/windows-10/x64/{name}"), key=lambda p: p.stat().st_mtime)
    if not found:
        sys.exit(
            f"\n{name} is not in electron-builder's cache.\n"
            "Build the package first: python scripts/build_msix.py"
        )
    return found[-1]


def publisher_from_config() -> str:
    appx = yaml.safe_load(BUILDER_CONFIG.read_text(encoding="utf-8")).get("appx") or {}
    publisher = appx.get("publisher")
    if not publisher or str(publisher).startswith("PLACEHOLDER"):
        sys.exit("\nNo real `publisher` in ui/electron-builder.yml — see docs/MSSTORE.md")
    return str(publisher)


def newest_package() -> Path:
    packages = sorted(RELEASE.glob("*.appx"), key=lambda p: p.stat().st_mtime)
    if not packages:
        sys.exit("\nNo .appx in release/ — run: python scripts/build_msix.py")
    return packages[-1]


def run(cmd: list[str], what: str) -> None:
    print(f"    $ {Path(cmd[0]).name} {' '.join(cmd[1:])}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"\n{what} failed:\n{(result.stdout + result.stderr).strip()[-1500:]}")


def main() -> int:
    if sys.platform != "win32":
        sys.exit("Windows only.")

    publisher = publisher_from_config()
    package = newest_package()
    WORK.mkdir(parents=True, exist_ok=True)

    pvk, cer, pfx = WORK / "test.pvk", WORK / "test.cer", WORK / "test.pfx"
    signed = WORK / package.name

    print(f"package:   {package.relative_to(ROOT)}  ({package.stat().st_size / 1e9:.2f} GB)")
    print(f"publisher: {publisher}")

    if not pfx.exists():
        print("\n1/3  making a test certificate")
        # -r self-signed, -h 0 no sub-CAs, -eku code-signing, -sv writes the key.
        run(
            [
                str(find_tool("makecert.exe")),
                "-r", "-h", "0",
                "-n", publisher,
                "-eku", "1.3.6.1.5.5.7.3.3",
                "-pe", "-sv", str(pvk),
                str(cer),
            ],
            "makecert",
        )
        run(
            [str(find_tool("pvk2pfx.exe")), "-pvk", str(pvk), "-spc", str(cer), "-pfx", str(pfx)],
            "pvk2pfx",
        )
    else:
        print("\n1/3  reusing the existing test certificate")

    print("\n2/3  signing a copy (the original stays unsigned for Partner Center)")
    shutil.copyfile(package, signed)
    run(
        [str(find_tool("signtool.exe")), "sign", "/fd", "SHA256", "/a", "/f", str(pfx), str(signed)],
        "signtool",
    )

    print("\n3/3  done\n")
    print(f"    signed copy:  {signed.relative_to(ROOT)}")
    print(f"    upload THIS:  {package.relative_to(ROOT)}   <- unsigned, for Partner Center")
    print("\nTo install it for testing, in an ADMIN PowerShell:\n")
    print(f'    Import-Certificate -FilePath "{cer}" `')
    print('        -CertStoreLocation Cert:\\LocalMachine\\TrustedPeople')
    print(f'    Add-AppxPackage "{signed}"')
    print("\nTo remove it again:\n")
    print("    Get-AppxPackage *ClipsStudio* | Remove-AppxPackage")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
