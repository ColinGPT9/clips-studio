# winget manifests

`winget install ColinGPT9.ClipsStudio` — once these are accepted.

These three files are the whole submission. They point at the installer that
already ships on GitHub Releases, so nothing about the build changes: no
account, no certificate, no certification queue. This is the cheapest
distribution channel the project has.

## Submitting a new version

1. Update `PackageVersion` in all three files, and in
   `ColinGPT9.ClipsStudio.installer.yaml` update `InstallerUrl`,
   `InstallerSha256` and `ReleaseDate`.

   The hash must be the real one for the file at that URL:

   ```bash
   python -c "import hashlib,urllib.request as u; print(hashlib.sha256(u.urlopen('URL').read()).hexdigest().upper())"
   ```

2. Fork [microsoft/winget-pkgs](https://github.com/microsoft/winget-pkgs) and
   copy the three files to:

   ```
   manifests/c/ColinGPT9/ClipsStudio/<version>/
   ```

3. Validate before opening the pull request:

   ```
   winget validate --manifest manifests/c/ColinGPT9/ClipsStudio/<version>
   winget install --manifest manifests/c/ColinGPT9/ClipsStudio/<version>
   ```

   The second one actually installs it. Do that in a VM or Windows Sandbox
   rather than on your own machine — `scripts/test-install.wsb` is already set
   up for exactly this.

4. Open the pull request. An automated pipeline validates and test-installs it,
   then a moderator reviews.

Alternatively `wingetcreate update ColinGPT9.ClipsStudio --version <v> --urls <url>`
does steps 1 and 2 and opens the pull request for you.

## Two things that could get it rejected

**The installer is a web installer.** `ClipsStudio-Web-Setup-<v>.exe` is under a
megabyte and downloads roughly 2 GB from Hugging Face when it runs. winget
itself is fine with that — it just runs the installer — but the validation
pipeline installs the package in a VM on a timer, and a 2 GB download inside
that window is the most likely reason for a failed check. If it times out, say
so in the pull request; this is a known situation for large apps and reviewers
deal with it regularly.

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
