"""YouTube Shorts upload via the YouTube Data API v3.

The only external API in the system. One-time setup per user:
  1. Google Cloud Console -> create project -> enable "YouTube Data API v3"
  2. OAuth consent screen -> add yourself as a test user
  3. Credentials -> OAuth client ID -> Desktop app -> download JSON
     -> save as config/client_secret.json
  4. python main.py auth   (opens browser once; token cached locally)

Quota reality: each upload costs 1,600 of the default 10,000 daily units
(hence the 6/day scheduler cap). Until Google verifies the app, API uploads
are locked private by YouTube regardless of the requested privacy — that is
YouTube policy, not a bug.
"""

from pathlib import Path

from publish.base import Publisher

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


class YouTubeShortsPublisher(Publisher):
    def __init__(self, client_secret: Path, token_path: Path, privacy: str = "unlisted"):
        self.client_secret = client_secret
        self.token_path = token_path
        self.privacy = privacy
        self._service = None

    @property
    def name(self) -> str:
        return "youtube_shorts"

    # ---- auth ----------------------------------------------------------

    def authenticate(self, interactive: bool = False):
        """Returns valid credentials. interactive=True may open a browser
        (the `auth` command); False raises if no cached token exists (daemon)."""
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        creds = None
        if self.token_path.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            self._save_token(creds)

        if not creds or not creds.valid:
            if not interactive:
                raise RuntimeError(
                    "No YouTube authorization yet. Run once:  python main.py auth"
                )
            if not self.client_secret.exists():
                raise RuntimeError(
                    f"Missing {self.client_secret}. Follow README 'Enable uploads' "
                    "to create OAuth credentials in Google Cloud Console."
                )
            from google_auth_oauthlib.flow import InstalledAppFlow

            flow = InstalledAppFlow.from_client_secrets_file(str(self.client_secret), SCOPES)
            creds = flow.run_local_server(port=0)
            self._save_token(creds)

        return creds

    def _save_token(self, creds) -> None:
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(creds.to_json(), encoding="utf-8")

    # ---- upload ----------------------------------------------------------

    def upload(self, video_path: Path, title: str, description: str, tags: list[str]) -> str:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

        if self._service is None:
            self._service = build("youtube", "v3", credentials=self.authenticate())

        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:4900],
                "tags": [t.lstrip("#") for t in tags][:30],
                "categoryId": "22",  # People & Blogs
            },
            "status": {
                "privacyStatus": self.privacy,
                "selfDeclaredMadeForKids": False,
            },
        }
        media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True)
        request = self._service.videos().insert(
            part="snippet,status", body=body, media_body=media
        )

        response = None
        while response is None:
            _, response = request.next_chunk()
        return response["id"]
