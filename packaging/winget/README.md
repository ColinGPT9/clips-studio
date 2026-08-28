# winget manifests

`winget install ColinGPT9.ClipsKitty` — once these are accepted.

These three files are the whole submission. They point at the installer that
already ships on GitHub Releases, so nothing about the build changes: no
account, no certificate, no certification queue. This is the cheapest
distribution channel the project has.

## Submitting a new version

1. Update `PackageVersion` in all three files, and in
   `ColinGPT9.ClipsKitty.installer.yaml` update `InstallerUrl`,
   `InstallerSha256` and `ReleaseDate`.

   The hash must be the real one for the file at that URL:

   ```bash
   python -c "import hashlib,urllib.request as u; print(hashlib.sha256(u.urlopen('URL').read()).hexdigest().upper())"
   ```

2. Fork [microsoft/winget-pkgs](https://github.com/microsoft/winget-pkgs) and
   copy the three files to:

   ```
   manifests/c/ColinGPT9/ClipsKitty/<version>/
   ```

3. Validate before opening the pull request:

   ```
   winget validate --manifest manifests/c/ColinGPT9/ClipsKitty/<version>
   winget install --manifest manifests/c/ColinGPT9/ClipsKitty/<version>
   ```

   The second one actually installs it. Do that in a VM or Windows Sandbox
   rather than on your own machine — `scripts/test-install.wsb` is already set
   up for exactly this.

4. Open the pull request. An automated pipeline validates and test-installs it,
   then a moderator reviews.

Alternatively `wingetcreate update ColinGPT9.ClipsKitty --version <v> --urls <url>`
does steps 1 and 2 and opens the pull request for you.

## Two things that could get it rejected

**The installer is a web installer.** `ClipsKitty-Web-Setup-<v>.exe` is under a
megabyte and downloads the payload from Hugging Face when it runs — **5.8 GB as
of 1.1.4**, not the 2 GB this file used to say, because the bundled Ollama
runtime and Whisper weights arrived since. winget itself is fine with that; it
just runs the installer. But the validation pipeline installs the package in a
VM on a timer, and a download that size inside that window is much the most
likely reason for a failed check. If it times out, say so in the pull request;
this is a known situation for large apps and reviewers deal with it regularly.

**The installer is unsigned.** No code signing certificate exists for this
project yet. winget does not require one, unlike the Microsoft Store's EXE/MSI
route, but SmartScreen will still warn users on first run until the installer
builds reputation.

## Why this exists alongside the Microsoft Store

They are different audiences and different amounts of work. winget reaches
people who install everything from a terminal and costs nothing to maintain.
The Store reaches people who never open one, and costs an MSIX build and a
certification pass per release — see `docs/MSSTORE.md`.

Neither replaces GitHub Releases, which stays the primary channel.
