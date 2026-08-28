# Publishing Clips Kitty to the Microsoft Store

Follow this with Partner Center open in another window. It assumes you have
never submitted an app before.

The Store exists here for one reason: people who would never find the GitHub
repository. It does not replace GitHub Releases, which stays the primary
channel, and the standalone installer keeps working exactly as it does today.

---

## Before you start: what this costs

**Nothing, as of the current Microsoft onboarding.** The registration fee for
both Individual and Company accounts has been removed. What it costs instead is
identity verification and your time.

You will need:

- A Microsoft account
- A government-issued ID and a phone that can take a selfie (Individual account)
- Roughly an hour of packaging, plus however long the upload takes

Pick **Individual**, not Company. Microsoft's own guidance says Individual is
for "independent developers whose distribution of apps through the Store is not
in relation to their business, trade, or profession", which is this. Note that
Individual cannot later be converted to Company — you would have to create a
new account.

---

## Step 1 — Create the developer account

Go to **<https://storedeveloper.microsoft.com>** and click *Get started for
free*.

That URL matters. It is the only entry point that reaches the new, free
onboarding flow; going in through Partner Center, Visual Studio or Xbox gets
you the old flow instead.

1. Choose **Individual developer**
2. Sign in with your Microsoft account
3. Verify your identity — government ID plus a selfie, done on a phone
4. Fill in your profile
5. Click **Go to Partner Center dashboard**

If the Apps & Games tile does not appear immediately, wait five minutes and
refresh. It can take up to 30 minutes for verification to propagate.

### If it asks you to pay, you are in the wrong flow

Microsoft's FAQ is explicit: *"You must begin your journey at
https://storedeveloper.microsoft.com. This is the only supported entry point
for the new flow. Other paths (e.g. direct via Partner Center, Xbox, or Visual
Studio) will show the legacy flow."*

The legacy flow charges the old registration fee. If you see one, close it and
start again from that URL. Do not enter payment details.

A **payout account** and **tax profile** are a different thing and are also not
needed: those exist to pay money *to* you, and only matter for paid apps or
in-app purchases. Clips Kitty is free, so skip them.

### The publisher display name is public and permanent

The publisher display name is what customers see on the listing, and **neither
it nor the account type can be changed after registration.** Individual
accounts are described by Microsoft as publishing "under your own name".

Use **ColinGPT9** — it is already what the repository, the website, the winget
manifest and the copyright line say, so anything else creates a second identity
to keep straight.

What is *not* published: Store policy 10.14 requires customer support contact
information to appear on the product page for **Company** accounts "in certain
regions", and states no such requirement for Individual accounts. The
trader/business-verification rules that come from the EU Digital Services Act
are likewise tied to Company accounts — Individual is explicitly the
hobbyist and non-commercial category.

If you want to publish under a name that is not your legal one, ask
[developer support](https://aka.ms/windowsdevelopersupport) **before** finishing
signup. It cannot be undone afterwards.

## Step 2 — Reserve the name

In Partner Center: **Apps and games → New product → MSIX or PWA app**.

Search for **Clips Kitty** and reserve it if it is free. If it is taken, try
`Clips Kitty - AI Video Clipper`. Do not add descriptive words just for search
reach — Store policy 10.1.1 says the product name "must not contain marketing
or descriptive text, including extraneous use of keywords", and a name that
breaks it fails certification.

The name is reserved to you as soon as you claim it, before any submission
exists.

## Step 3 — Copy your identity values

**Done.** Recorded here so they can be checked without logging in, and so a
future release does not have to go looking. None of these are secrets — every
one ships inside the manifest of the package itself.

**Where they came from:** Partner Center → Clips Kitty → General → **View
product identity**.

| Partner Center calls it | Goes into | Value |
|---|---|---|
| Package/Identity/Name | `identityName` | `ClipsStudio.ClipsStudio` |
| Package/Identity/Publisher | `publisher` | `CN=82A1C822-C6B7-41D5-889B-160627060939` |
| Package/Properties/PublisherDisplayName | `publisherDisplayName` | `Clips Studio` |
| Package Family Name | *(nothing — quote it in support requests)* | `ClipsStudio.ClipsStudio_315g1r74a6w58` |
| Store ID | *(the links below)* | `9NB6XT7DSQZZ` |

Once the product is live, the Store ID gives you these — add them to the README
and the website then, not before, because they 404 until it publishes:

- Web listing: `https://apps.microsoft.com/detail/9NB6XT7DSQZZ`
- Opens the Store app directly: `ms-windows-store://pdp/?productid=9NB6XT7DSQZZ`

**The publisher display name is "Clips Kitty", not a personal name**, so the
listing does not show who owns the account.

A mismatch in any of the three is not caught at build time. It fails at upload,
after the package has already been built, which is why `build_msix.py` checks
they are at least present before it starts.

`applicationId: ClipsStudio` is ours, not Microsoft's. Never change it: it is
part of the app's identity to Windows, and changing it makes the next release
look like a different app.

`scripts/build_msix.py` refuses to run while any placeholder is still there, so
you cannot accidentally build a package that cannot be uploaded.

## Step 4 — Build the package

```
python scripts/build_msix.py
```

**Windows Developer Mode must be on** (*Settings → System → For developers*).
The script checks and stops with instructions if it is off. It is needed twice:
electron-builder unpacks `makeappx.exe` from an archive containing macOS
symlinks that an ordinary account cannot extract, and installing an unsigned
package for testing requires it as well.

`--skip-backend` reuses the frozen Python engine from a previous build, which
saves several minutes when you are only changing packaging.

The result is `release/Clips Kitty-<version>-x64.appx`, around 6 GB.

### About the version number

The Store will not accept `0.1.2.0`. Its rule is that the fourth part must be 0
and the first part **cannot** be 0. So the script derives the package version
by adding one to the major:

| App version | MSIX package version |
|---|---|
| 0.1.2 | **1.1.2.0** |
| 0.1.3 | 1.1.3.0 |
| 0.2.0 | 1.2.0.0 |
| 1.0.0 | 2.0.0.0 |

The leading `1` is a Store requirement, not a claim that this is a 1.0 release.
Everything a user sees — the README, the GitHub release, this site, the Store
description — still says **0.1.2**.

It has to derive from the app's major rather than being pinned at 1, because
the Store also requires every submission to be higher than the last. If the
major were fixed at 1, then shipping app version 1.0.0 would produce `1.0.0.0`,
which sorts *below* the `1.1.2.0` already published, and the update would be
rejected.

### Installing it yourself before uploading

The package is unsigned on purpose — Microsoft re-signs it after certification,
so no certificate is needed to submit. But Windows will not install an unsigned
package, so testing what you are about to ship means signing it first:

```
python scripts/sign_msix.py
```

**No Windows SDK needed.** `signtool.exe` ships inside electron-builder's
winCodeSign download, which is already on disk from building the package, and
the certificate comes from PowerShell's `New-SelfSignedCertificate`, which is
built into Windows.

(The same cache contains `makecert.exe`, and the script deliberately does not
use it: `makecert -sv` opens a GUI password prompt and hangs anything running
unattended.)

The script signs a **copy** into `build/msix-signing/`, so the original in
`release/` stays unsigned and is still the file to upload. It prints the two
commands to install it, which need an **admin** PowerShell:

```powershell
Import-Certificate -FilePath "build\msix-signing\test.cer" `
    -CertStoreLocation Cert:\LocalMachine\TrustedPeople
Add-AppxPackage "build\msix-signing\ClipsStudio-1.1.2-x64.appx"
```

To remove it again: `Get-AppxPackage *ClipsStudio* | Remove-AppxPackage`

The certificate subject must equal `publisher` exactly, so the script reads it
from `electron-builder.yml` rather than having it typed twice — a mismatch
makes Windows refuse the install with an error that names neither value.

Then run the [Windows App Certification Kit](https://learn.microsoft.com/en-us/windows/uwp/debug-test-perf/windows-app-certification-kit)
against the installed package before uploading.

## Step 5 — Create the submission

From the app overview, click **Start submission**.

### Pricing and availability

- **Price:** Free
- **Markets:** all
- **Discoverability:** *Make this product available and discoverable in the
  Store* — the whole point of being here
- **Schedule:** publish manually. **Do not** let it go live automatically on
  the first submission; you want to read the certification report first.

### Properties

- **Category:** primary **Photo + video**; secondary **Multimedia design →
  Photo + video production**.

  An earlier version of this page said "Multimedia design → Video editing",
  which is wrong twice over and produced a miscategorised listing. There is no
  **Video editing** subcategory anywhere in the Store — Multimedia design offers
  only Illustration + graphic design, Music production, and Photo + video
  production — so the instruction named something unselectable.

  And Multimedia design is the wrong parent. Microsoft describes it as tools for
  "creating or editing **graphics, art, design**", with examples that are
  entirely image editing, painting, sketchbooks, 3D modelling and fine arts —
  no video at all. **Photo + video** is described as "capturing, **editing**,
  and sharing photos or **videos**", and lists photo/video editing outright.

  Non-Games apps get a secondary category from the same list, so both can be
  claimed rather than traded. See
  [categories and subcategories](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/categories-and-subcategories).

  Changing this later is a metadata-only submission: no package upload, the
  published listing stays live throughout, and with manual publishing it only
  changes when you click.
- **Privacy policy URL:** `https://colingpt9.github.io/clips-studio/privacy.html`
  This is **required** — Store policy 10.5.1 says Win32 and Desktop Bridge
  products "must always have privacy policies".
- **Website:** `https://colingpt9.github.io/clips-studio/`
- **Support contact:** `https://github.com/ColinGPT9/clips-studio/issues`

System requirements, taken from the README rather than invented:

| | |
|---|---|
| Minimum RAM | 16 GB |
| Recommended | NVIDIA GPU |
| Disk | 20 GB, plus room for videos |
| Architecture | x64 |
| OS | Windows 10 1809 or later |

**Product declarations:** tick that the product uses **live generative AI**.
Policy 11.16 requires it — the app writes clip titles with a language model in
response to user input — and it requires the metadata to disclose it and a way
for users to report bad output. The in-app Feedback Hub covers the last part.

**Do not declare a third-party purchase API.** An earlier version of this page
said to, and that was wrong. Nothing in Clips Kitty is for sale: the donate
button is optional, it buys nothing, and in Store builds it hands PayPal to the
*system browser* rather than opening a payment page inside the app (see
`ui/src/main/distribution.ts`). No purchase API is used and no transaction
happens in the app, so there is nothing to declare — declaring one would invite
questions about a payment flow that does not exist.

The browser handoff is still the right behaviour and stays: policy 10.8.2 is
explicit that "users may be directed to a browser to complete registration or
transactions", and it means a certification tester never has to assess a
payment page hosted inside the app.

### Age rating

Complete the IARC questionnaire honestly. For Clips Kitty the answers are all
"no" — no violence, no sexual content, no gambling, no profanity generated by
the app, no user-to-user communication, no location sharing, no advertising.

Two that need thought rather than a reflex "no":

- **Does the app let users share content?** No. It writes files to the user's
  own disk. It does not post anywhere, and the optional YouTube upload path is
  disabled, CLI-only, and requires credentials the user creates themselves.
- **Does the app display user-generated or uncurated content?** The user's own
  video, to the user. Nothing from other users.

Expect PEGI 3 / ESRB Everyone.

### Packages

Upload `release/Clips Kitty-<version>-x64.appx`.

Microsoft's docs recommend `.msixupload` for Store submissions, but that
recommendation is for **UWP apps packaged in Visual Studio**, where the wrapper
carries a `.appxsym` symbol file for crash analytics. An Electron app has no
PDBs to contribute, so the wrapper would add nothing. Partner Center accepts
`.appx` and `.msix` directly.

### Store listing

Copy from [`store-listing.md`](store-listing.md).

Required: a description and at least one screenshot. Provide four or more —
they are what people actually look at.

### Submission options

Put the certification tester's instructions in **Notes for certification**.
This is the single highest-value field in the whole submission, because what
the tester will see on first run is unusual:

> Clips Kitty processes video locally using an AI model that is downloaded on
> first run, so the first launch shows a model download of roughly 3-5 GB with
> a progress bar. This is model weights (data), not executable code. Nothing is
> uploaded; all processing is on-device.
>
> To test without waiting for a download, paste any short YouTube link — the
> app will prompt to download a model first. A machine with 16 GB of RAM is
> required; on less, clips are analysed but rendering fails with an
> out-of-memory error.
>
> No account or login is needed. The optional YouTube publishing feature is
> disabled by default, is command-line only, and requires Google Cloud
> credentials the user supplies themselves, so it is not reachable in this
> build.
>
> Setup picks the AI model to download from the test machine's hardware, so on
> a PC without a graphics card it installs a smaller one. The "AI model" row
> then names whichever model was installed — it is not expected to name any
> particular model, and a name that differs from any documentation is correct
> rather than a failure.
>
> The donate button opens PayPal in the system browser. It is an optional
> donation: nothing is sold in the app, and no purchase API is used.

The AI-model paragraph is there because its absence cost a cycle. The
**10.1.2.10** rejection was a tester who downloaded the model setup recommended
for their hardware, saw the check name a different one, and reported the
feature as unusable. The check no longer does that (see the 1.1.4 changelog),
but a tester reads the notes before they read the screen.

## Step 6 — Submit, and what happens next

Click **Submit to the Store**.

Certification usually takes a few hours to three days. Status appears on the
submission page and you get email at each stage.

**If it fails**, the report names the policy number. Fix it, then use **Update**
on the submission — you do not start over. Microsoft's own published statistics
show 623 overturned decisions out of 1,118 appeals, so if a rejection looks
wrong, appealing is worthwhile: `reportapp@microsoft.com`.

**If it passes**, it will not go live until you publish it, because you chose
manual publishing. Read the report, then publish.

## Later releases

Do **not** create a new product. From the app overview, click **Start update**,
which copies the previous submission so you only change what moved. A new
product would throw away the ratings, reviews and Store URL.

1. Bump the version in `ui/package.json` as usual
2. `python scripts/build_msix.py`
3. **Start update** in Partner Center, upload the new package, submit

Ratings and reviews carry across, and existing users update automatically.

## What the Store build does differently

Both distributions are the same binary content. Two behaviours differ, decided
at runtime in `ui/src/main/distribution.ts` by checking `process.windowsStore`
— a fact Electron sets from the package itself, so it cannot disagree with
reality:

| | Standalone | Store |
|---|---|---|
| Updates | electron-updater, Hugging Face feed | the Store |
| Donate button | PayPal in a locked-down in-app window | PayPal in the system browser |

Everything else — the engine, the models, the pipeline, the data directory — is
identical.

## Known gaps

- **The package is around 6 GB**, mostly CUDA PyTorch. Under the Store's 25 GB
  cap, but every release means re-uploading it.
- **It cannot be built in CI.** A GitHub-hosted runner has 14 GB of disk; the
  unpacked app alone is larger than that. Building the Store package is a local
  job until there is a self-hosted runner.
- **AI dubbing ships as of 1.1.3.** Piper is bundled, so `dub.available()`
  is True in a packaged build. Voices are not bundled — each is ~60 MB and is
  downloaded into `data/voices/` the first time a language is dubbed, so
  dubbing needs a working connection once per language.
