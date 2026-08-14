# Microsoft Store 0.1.2 — first submission checklist

Work top to bottom. Everything above the line is done; everything below needs
you, mostly because it needs a Partner Center account only you can create.

Full instructions for each step are in [MSSTORE.md](MSSTORE.md).

## Done

- [x] **Existing installer untouched** — `win.target` is unchanged, so
      `python scripts/build_installer.py` produces exactly what it did before
- [x] **Config no longer written into the install directory** — would have made
      model switching fail inside an MSIX package (`core/paths.py`)
- [x] **Packaged app verified to launch** — window, backend and bundled Ollama
      all come up
- [x] **appx target added** (`ui/electron-builder.yml`), with `runFullTrust` as
      the only capability
- [x] **Build script** — `python scripts/build_msix.py`
- [x] **Version mapping** — 0.1.2 → package `1.1.2.0`, verified monotonic past
      the 1.0.0 boundary
- [x] **Store tiles generated** — `python scripts/build_appx_assets.py`
- [x] **Updates are distribution-aware** — a Store copy never runs the NSIS
      updater
- [x] **Donations open in the system browser** on Store builds (policy 10.8.2)
- [x] **Privacy policy published** — required by policy 10.5.1, and Partner
      Center will not accept a submission without the URL
- [x] **Store listing copy drafted** — [store-listing.md](store-listing.md)
- [x] **Certification notes drafted** — in MSSTORE.md, covering the first-run
      model download that would otherwise confuse a tester

---

## Needs you

### Account and identity

- [x] Developer account created at **<https://storedeveloper.microsoft.com>**
      (free; that URL specifically, or you get the old paid flow — if you are
      asked for a registration fee, you are in the legacy flow, so back out)
- [x] Publisher display name settled: **Clips Studio**. It is public and cannot
      be changed later, and it is not a personal name, so the listing does not
      identify the account holder.
- [x] Identity verified (government ID + selfie)
- [x] Product name reserved: **Apps and games → New product → MSIX or PWA app**
- [x] Three values copied from **General → View product identity** into the
      `appx:` block of `ui/electron-builder.yml` — done, and recorded in
      MSSTORE.md so a future release need not go looking

`scripts/build_msix.py` refuses to run until those are filled in.

### Build and test

- [x] Windows Developer Mode on (*Settings → System → For developers*)
- [x] `python scripts/build_msix.py` completes
- [x] `AppxManifest.xml` shows `1.1.2.0`, `ProcessorArchitecture="x64"`,
      `runFullTrust` and nothing else — verified by reading the manifest out of
      the built package
- [x] Package signed and installed locally — `python scripts/sign_msix.py`,
      then the two commands it prints. No Windows SDK needed.
- [x] Installed package launches (window, engine and bundled Ollama all start)
- [ ] Import a video, process it, export — **not done end to end in the
      package**; only the launch and model paths were exercised
- [x] Model switching works **inside the package** — `/models/activate`
      returns 200 and the choice persists. It returned **500** before the
      config fix, so this is the one that mattered.
- [ ] Restart, uninstall, reinstall
- [x] Nothing written into the package directory — settings land in
      `%LOCALAPPDATA%\Clips Studio\settings.yaml`, confirmed after a switch
- [ ] Windows App Certification Kit passes — **cannot run here**, `appcert.exe`
      ships with the Windows SDK which is not installed. Optional, but it
      catches manifest problems before Microsoft's testers do.

### Submission

- [ ] Pricing: **Free**
- [ ] Markets: all
- [ ] Discoverability: available and discoverable in the Store
- [ ] Category: Multimedia design → Video editing
- [ ] Privacy policy URL:
      `https://colingpt9.github.io/clips-studio/privacy.html`
- [ ] System requirements entered — **16 GB RAM minimum**, x64, Windows 10 1809+
- [ ] Product declaration: **live generative AI** ticked (policy 11.16)
- [ ] Product declaration: **third-party purchase API** ticked, for the PayPal
      donate link (policy 10.8.2)
- [ ] Age rating questionnaire completed
- [ ] Package uploaded (`release/*.appx`)
- [ ] Listing: description, feature bullets, search terms
- [x] Four screenshots ready in `docs/store-screenshots/` — dashboard and
      queue from the Store build, models and settings from the dev build. None
      show a face, a video title or a channel name.
- [ ] 1:1 box art uploaded — `docs/store-art/box-art-1x1.png`
- [ ] 2:3 poster art uploaded — `docs/store-art/poster-art-2x3.png` (this is
      what the Store shows in most browsing surfaces)
- [ ] Certification notes pasted in
- [ ] **Publishing set to manual, not automatic** — so you read the
      certification report before it goes live
- [ ] Submitted

### After it passes

- [ ] Certification report read
- [ ] Published
- [ ] Store link added to the README and the website —
      `https://apps.microsoft.com/detail/9NB6XT7DSQZZ` (404s until it publishes)
- [ ] `CHANGELOG.md` notes that 0.1.2 is on the Store

## Not blocking, but worth doing

- [ ] **winget** — [`packaging/winget/`](../packaging/winget/) is ready to
      submit and needs no account, certificate or certification. Much cheaper
      than the Store and reaches a different audience.
- [ ] Screenshots and a demo video ([#35](../../issues/35)) — the listing needs
      screenshots anyway, so this closes two things at once.
