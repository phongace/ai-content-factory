"""YouTube Data API v3 publisher. OAuth "installed app" flow, token stored locally in
data/secrets/youtube_token.json (gitignored) so it survives restarts (P0-14 acceptance)."""

from __future__ import annotations

import time
from pathlib import Path

from ..config import Settings, get_settings
from ..store import atomic_write_text
from .base import NotConfigured, PublishError, PublishRequest, PublishResult, build_description

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
RETRY_STATUSES = frozenset({500, 502, 503, 504})
MAX_ATTEMPTS = 5  # consecutive failures tolerated per chunk; a successful chunk resets the count
BACKOFF_BASE_SECONDS = 1.0
_sleep = time.sleep  # module-level so tests can patch it


def _client_config(s: Settings) -> dict:
    return {
        "installed": {
            "client_id": s.youtube_client_id,
            "client_secret": s.youtube_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def status(settings: Settings | None = None) -> str:
    s = settings or get_settings()
    if not s.youtube_configured:
        return "not_configured"
    if not s.youtube_token_file.is_file():
        return "not_connected"
    from google.auth.exceptions import GoogleAuthError

    try:
        _credentials(s)  # refreshes only when the access token expired; no network call while it is valid
    except (NotConfigured, GoogleAuthError, ValueError):  # revoked/expired refresh token, offline, corrupt file
        return "not_connected"
    return "connected"


def connect(settings: Settings | None = None, port: int = 8765, open_browser: bool = True) -> Path:
    """Run the OAuth consent in the browser once and persist the refresh token."""
    s = settings or get_settings()
    if not s.youtube_configured:
        raise NotConfigured("YouTube not configured: set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET")
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_config(_client_config(s), SCOPES)
    creds = flow.run_local_server(port=port, open_browser=open_browser, access_type="offline", prompt="consent")
    atomic_write_text(s.youtube_token_file, creds.to_json())
    s.youtube_token_file.chmod(0o600)
    return s.youtube_token_file


def _credentials(s: Settings):
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not s.youtube_token_file.is_file():
        raise NotConfigured("YouTube channel not connected: run `uv run factory youtube-connect`")
    creds = Credentials.from_authorized_user_file(str(s.youtube_token_file), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError as e:  # e.g. invalid_grant: Testing-mode refresh tokens expire after ~7 days
                reason = e.args[0] if e.args else e
                raise NotConfigured(
                    f"YouTube token expired or revoked ({reason}): run `uv run factory youtube-connect` again"
                ) from e
            atomic_write_text(s.youtube_token_file, creds.to_json())
        else:
            raise NotConfigured("YouTube token invalid: run `uv run factory youtube-connect` again")
    return creds


def _upload_chunks(request) -> dict:
    """Drive a resumable upload to completion. After an error `next_chunk()` asks YouTube for the committed
    offset first, so a retry continues from where the upload stopped instead of restarting the file."""
    import httplib2
    from googleapiclient.errors import HttpError

    failures = 0
    while True:
        try:
            _, response = request.next_chunk()
        except HttpError as e:
            reason = f"YouTube API error {e.status_code}: {e.reason}"
            if e.status_code not in RETRY_STATUSES:
                raise PublishError(reason) from e
            err: Exception = e
        except (OSError, httplib2.HttpLib2Error) as e:  # socket/SSL/connection resets, DNS failures
            reason = f"network error {type(e).__name__}: {e}"
            err = e
        else:
            failures = 0
            if response is not None:
                return response
            continue
        failures += 1
        if failures >= MAX_ATTEMPTS:
            raise PublishError(f"{reason} (gave up after {MAX_ATTEMPTS} attempts)") from err
        _sleep(BACKOFF_BASE_SECONDS * 2 ** (failures - 1))


class YouTubePublisher:
    name = "youtube"

    def __init__(self, settings: Settings | None = None):
        self.s = settings or get_settings()
        if not self.s.youtube_configured:
            raise NotConfigured("YouTube not configured: set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET")

    def upload(self, req: PublishRequest) -> PublishResult:
        if req.privacy not in ("private", "unlisted"):
            raise PublishError("POC only uploads private or unlisted; switch to public manually in Studio")
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

        youtube = build("youtube", "v3", credentials=_credentials(self.s), cache_discovery=False)
        title = req.title.strip()[:100]
        body = {
            "snippet": {
                "title": title,
                "description": build_description(req.description, req.hashtags),
                "tags": [h.lstrip("#") for h in req.hashtags][:15],
                "categoryId": "22",  # People & Blogs
                "defaultLanguage": req.language,
            },
            "status": {"privacyStatus": req.privacy, "selfDeclaredMadeForKids": False},
        }
        media = MediaFileUpload(str(req.file), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        response = _upload_chunks(request)
        vid = response["id"]
        actual = (response.get("status") or {}).get("privacyStatus")  # unverified API projects are forced to private
        return PublishResult(
            vid,
            f"https://youtube.com/shorts/{vid}",
            response,
            actual if actual in ("private", "unlisted") else None,
        )
