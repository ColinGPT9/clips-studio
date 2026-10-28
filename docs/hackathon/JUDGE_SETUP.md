# Run the submitted workflow

Use Windows, Python 3.11+, Node 20+ and FFmpeg. The full project setup and optional
local model setup are documented in the app repository README. Twitch metadata
and EventSub do not require a paid model provider or GPU.

```powershell
git clone --branch feature/twitch-vod-binding https://github.com/ColinGPT9/clips-studio.git
git clone --branch improve/gta-concise-moments https://github.com/ColinGPT9/Grand-Theft-Auto-6-Clips-Kitty-Plugin.git
cd clips-studio
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
cd ui
npm.cmd ci
npm.cmd run build
npm.cmd run dev
```

For a quick Twitch-only check, choose **Skip setup** in the first-run model
wizard. Open Settings > Connected accounts and paste this submission's registered
Public Twitch Client ID:

```text
0ihqn762rozi77qg12ofr7s7azjgj2
```

This identifies the same **Clips Kitty** application shown in
the demo; it is not a client secret or an account credential. Connect your own
Twitch account with **Read Twitch chat**, enter a channel name or URL in Viewer
highlight nominations, and explicitly start collection. Reading the selected
public channel does not grant broadcaster/editor VOD-clip rights.

The app ID is supplied for testing this same submission. To register a separate
application instead, use a Public client and the HTTPS localhost hint. Twitch
documents Client IDs as public in its
[registration guide](https://dev.twitch.tv/docs/authentication/register-app/).

The app stores credentials in the OS-protected store. Configure your own
connection; no credentials are included in the source archives. Collection does
not send chat or publish a clip. Stop collection before switching channels.

Install the detector from its cloned folder in Marketplace, or unzip the supplied
GTA-plugin-0.5.2-source.zip and install that folder. Use Gaming with the installed
detector for GTA/RP recordings. Other channels can use the regular finder. Set up
the local transcription/model dependencies described by the app before full
media processing. The detector's README also supplies authored synthetic examples.

The final demo uses xQc for Twitch features and for the GTA V/RP highlight.
The actual host processed a continuous eight-minute excerpt of the same Twitch
broadcast used by the live check, then exported a19.2-second vertical clip with
manual facecam framing. This is not a claim that live nominations generated the
highlight or that chat/VOD timing was verified. Gameplay and personal account
data are not included in these source archives. See docs/hackathon/TEST_LOG.md for the separate
real-service, real-media, synthetic and offline results.
