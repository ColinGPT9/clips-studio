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
    Nothing that is not already here. signtool.exe ships inside electron-builder's
    winCodeSign download, so the Windows SDK is not required, and the certificate
    is made by PowerShell's New-SelfSignedCertificate, which is built into
    Windows. Developer Mode must be on, which it must be to have built the
    package at all.

    Deliberately NOT makecert.exe, which is in that same cache: `makecert -sv`
    opens a GUI password dialog and hangs anything running unattended.

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

    cer = WORK / "test.cer"
    signed = WORK / package.name

    print(f"package:   {package.relative_to(ROOT)}  ({package.stat().st_size / 1e9:.2f} GB)")
    print(f"publisher: {publisher}")

    print("\n1/3  test certificate")
    # Created in the current user's certificate store and used from there, by
    # thumbprint. Deliberately NOT exported to a .pfx: a .pfx carries the
    # private key, so it needs a password, and that password would then have to
    # be handed to signtool on a command line this script prints. CodeQL was
    # right to flag that (py/clear-text-logging-sensitive-data); the fix is for
    # the secret not to exist rather than to hide it from the log.
    #
    # New-SelfSignedCertificate rather than the makecert.exe in the same cache:
    # makecert's -sv opens a GUI password prompt and hangs anything unattended.
    #   2.5.29.37 = Enhanced Key Usage, 1.3.6.1.5.5.7.3.3 = code signing
    script = f"""
$ErrorActionPreference = 'Stop'
$existing = Get-ChildItem Cert:\\CurrentUser\\My |
  Where-Object {{ $_.Subject -eq '{publisher}' }} |
  Sort-Object NotAfter -Descending | Select-Object -First 1
if ($existing) {{
  $c = $existing
  Write-Output "REUSED"
}} else {{
  $c = New-SelfSignedCertificate -Type Custom -Subject '{publisher}' `
    -KeyUsage DigitalSignature -FriendlyName 'Clips Studio test signing' `
    -CertStoreLocation 'Cert:\\CurrentUser\\My' `
    -TextExtension @('2.5.29.37={{text}}1.3.6.1.5.5.7.3.3', '2.5.29.19={{text}}')
  Write-Output "CREATED"
}}
Export-Certificate -Cert $c -FilePath '{cer}' | Out-Null
Write-Output $c.Thumbprint
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not cer.exists():
        sys.exit(f"\ncertificate setup failed:\n{(result.stdout + result.stderr)[-1500:]}")
    lines = result.stdout.strip().splitlines()
    thumbprint = lines[-1].strip()
    print(f"    {'reused' if 'REUSED' in result.stdout else 'created'}, thumbprint {thumbprint}")

    print("\n2/3  signing a copy (the original stays unsigned for Partner Center)")
    print(f"    copying {package.stat().st_size / 1e9:.1f} GB, this takes a minute")
    shutil.copyfile(package, signed)
    # /sha1 picks the key out of the user's certificate store, so no private key
    # and no password ever reach the command line.
    run(
        [
            str(find_tool("signtool.exe")), "sign",
            "/fd", "SHA256",
            "/sha1", thumbprint,
            str(signed),
        ],
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
