# Run the submitted workflow

Use Windows, Python3.11+, Node20+ and FFmpeg. The full project setup and optional
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

In Settings > Connected accounts, configure a Public Twitch application's Client
ID and use the HTTPS localhost registration hint. Connect your own Twitch account
with Read Twitch chat. Enter a channel name or URL in Viewer highlight nominations
and explicitly start collection. Viewer access can read the selected public
channel; it does not grant broadcaster/editor VOD-clip rights.

The app stores credentials in the OS-protected store. Configure your own
connection; no credentials are included in the source archives. Collection does
not send chat or publish a clip. Stop collection before switching channels.

Install the detector from its cloned folder in Marketplace, or unzip the supplied
GTA-plugin-0.5.2-6ab55bd.zip and install that folder. Use Gaming with the installed
detector for GTA/RP recordings. Other channels can use the regular finder. Set up
the local transcription/model dependencies described by the app before full
media processing. The detector's README also supplies authored synthetic examples.

The demo's original test footage, authored transcript and captions are technical
examples. Private third-party benchmark footage and personal account data are not
included in these source archives. See docs/hackathon/TEST_LOG.md for the separate
real-service, real-media, synthetic and offline results.
