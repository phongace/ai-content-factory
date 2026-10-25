"""YouTube Data API v3 publisher. OAuth "installed app" (Desktop client) flow.

The OAuth client comes from the Desktop-app JSON at `data/secrets/youtube_client_secret.json` (override with
YOUTUBE_CLIENT_SECRET_FILE), with the YOUTUBE_CLIENT_ID/YOUTUBE_CLIENT_SECRET env pair as a fallback. The user
token is stored locally in `data/secrets/youtube_token.json` (gitignored, mode 600) so it survives restarts
(P0-14 acceptance). Only the `youtube.upload` scope is requested, so read APIs such as `channels.list(mine=True)`
are deliberately never called. Neither the client secret nor a token is ever printed or logged."""

from __future__ import annotations

import io
import json
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests

from ..config import Settings, get_settings
from ..store import atomic_write_text
from .base import NotConfigured, PublishError, PublishRequest, PublishResult, build_description, clean_title

UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
SCOPES = [UPLOAD_SCOPE]
TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
TOKENINFO_TIMEOUT_SECONDS = 15
CONNECT_TIMEOUT_SECONDS = 300  # how long `youtube-connect` waits for the browser callback
CONNECT_CMD = "`uv run factory youtube-connect`"
CONSENT_BANNER = """HUMAN ACTION REQUIRED:

1. Chọn Google account quản lý YouTube channel cần test.
2. Bấm Continue / Allow.
3. Quay lại terminal sau khi Google báo authorization completed."""
AUTH_PROMPT = "If the browser did not open, visit this URL to authorize: {url}"
AUTH_SUCCESS = "Authorization completed. You can close this tab and return to the terminal."

RETRY_STATUSES = frozenset({500, 502, 503, 504})
MAX_ATTEMPTS = 5  # consecutive failures tolerated per chunk; a successful chunk resets the count
BACKOFF_BASE_SECONDS = 1.0
_sleep = time.sleep  # module-level so tests can patch it

# HttpError reasons (lower-cased) that get a dedicated, actionable message. 403s in these groups are not retried.
_API_DISABLED_REASONS = frozenset({"accessnotconfigured", "service_disabled"})
_SCOPE_REASONS = frozenset({"insufficientpermissions", "access_token_scope_insufficient"})
_QUOTA_REASONS = {
    "quotaexceeded": "quotaExceeded",
    "uploadlimitexceeded": "uploadLimitExceeded",
    "ratelimitexceeded": "rateLimitExceeded",
}


@dataclass(frozen=True)
class TokenInfo:
    """What Google's tokeninfo says about the current access token. Contains no secret."""

    scopes: tuple[str, ...]
    expires_in: int
    has_refresh_token: bool

    def as_dict(self) -> dict:
        expires_at = datetime.now(UTC) + timedelta(seconds=self.expires_in)
        return {
            "scopes": list(self.scopes),
            "access_token_expires_in_seconds": self.expires_in,
            "access_token_expires_at": expires_at.isoformat(timespec="seconds"),
            "has_refresh_token": self.has_refresh_token,
        }


# ------------------------------------------------------------------ OAuth client (credentials file)
def _missing_client_message(s: Settings) -> str:
    return (
        f"YouTube not configured: OAuth client file not found at {s.youtube_client_secret_file}. "
        "Create a Desktop app OAuth client in Google Cloud Console, download its JSON and save it there "
        "(or point YOUTUBE_CLIENT_SECRET_FILE at it; fallback: set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET)"
    )


def _env_client_config(s: Settings) -> dict:
    return {
        "installed": {
            "client_id": s.youtube_client_id,
            "client_secret": s.youtube_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def _validate_client_file(path: Path) -> None:
    """Fail early, with a retryable message, on an unreadable/malformed/wrong-type client JSON.
    Messages never echo file content (only whether it is a `web` client)."""
    redownload = (
        "download the Desktop app client JSON again from Google Cloud Console (Credentials > OAuth 2.0 Client IDs)"
    )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        raise NotConfigured(
            f"YouTube OAuth client file {path} cannot be read: check that it exists and is readable"
        ) from None
    except ValueError:
        raise NotConfigured(f"YouTube OAuth client file {path} is not valid JSON: {redownload}") from None
    if not isinstance(data, dict) or "installed" not in data:
        kind = "web" if isinstance(data, dict) and "web" in data else "unknown"
        raise NotConfigured(
            f"YouTube OAuth client file {path} is not a Desktop app client (found type: {kind}, expected `installed`): "
            "create an OAuth client of type Desktop app and save its JSON there"
        )
    client = data["installed"]
    missing = [
        key
        for key in ("client_id", "client_secret")
        if not (isinstance(client, dict) and isinstance(client.get(key), str) and client[key].strip())
    ]
    if missing:
        raise NotConfigured(f"YouTube OAuth client file {path} is missing {', '.join(missing)}: {redownload}")


def _build_flow(s: Settings):
    from google_auth_oauthlib.flow import InstalledAppFlow

    path = s.youtube_client_secret_file
    if path.is_file():
        _validate_client_file(path)
        return InstalledAppFlow.from_client_secrets_file(str(path), SCOPES)
    if s.youtube_client_from_env:
        return InstalledAppFlow.from_client_config(_env_client_config(s), SCOPES)
    raise NotConfigured(_missing_client_message(s))


# ------------------------------------------------------------------ token storage and verification
def _write_token(s: Settings, creds) -> None:
    atomic_write_text(s.youtube_token_file, creds.to_json())
    s.youtube_token_file.chmod(0o600)


def _credentials(s: Settings):
    from google.auth.exceptions import RefreshError, TransportError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not s.youtube_token_file.is_file():
        raise NotConfigured(f"YouTube channel not connected: run {CONNECT_CMD}")
    try:
        creds = Credentials.from_authorized_user_file(str(s.youtube_token_file), SCOPES)
    except (OSError, ValueError):
        raise NotConfigured(f"YouTube token file is unreadable or corrupt: run {CONNECT_CMD} again") from None
    if not creds.valid:
        if creds.refresh_token:  # access token expired or missing: refresh it transparently
            try:
                creds.refresh(Request())
            except RefreshError as e:  # e.g. invalid_grant: Testing-mode refresh tokens expire after ~7 days
                reason = e.args[0] if e.args else e
                raise NotConfigured(f"YouTube token expired or revoked ({reason}): run {CONNECT_CMD} again") from e
            except TransportError as e:
                raise PublishError(
                    f"could not reach Google to refresh the YouTube token (network error {type(e).__name__}): "
                    "check the connection and retry"
                ) from e
            _write_token(s, creds)
        else:
            raise NotConfigured(f"YouTube token invalid: run {CONNECT_CMD} again")
    return creds


def _tokeninfo(creds) -> TokenInfo:
    """Ask Google what the access token grants. The token only travels in this one request, and error messages
    never include the request URL (requests exceptions would embed the token query string)."""
    try:
        resp = requests.get(TOKENINFO_URL, params={"access_token": creds.token}, timeout=TOKENINFO_TIMEOUT_SECONDS)
    except requests.RequestException as e:
        raise PublishError(
            f"could not reach Google tokeninfo to verify the token (network error {type(e).__name__}): retry"
        ) from None
    if resp.status_code in (400, 401):
        raise NotConfigured(
            f"Google rejected the YouTube access token (tokeninfo HTTP {resp.status_code}): run {CONNECT_CMD} again"
        )
    if resp.status_code != 200:
        raise PublishError(f"Google tokeninfo returned HTTP {resp.status_code}: retry later")
    try:
        data = resp.json()
        scopes = tuple(str(data.get("scope", "")).split())
        expires_in = int(data.get("expires_in") or 0)
    except (ValueError, TypeError, AttributeError):
        raise PublishError("Google tokeninfo returned an unexpected response: retry later") from None
    if UPLOAD_SCOPE not in scopes:
        granted = ", ".join(scopes) or "none"
        raise NotConfigured(
            f"YouTube token lacks the youtube.upload permission (granted: {granted}): run {CONNECT_CMD} again "
            "and leave the upload permission checked on the Google consent screen"
        )
    if expires_in <= 0:
        raise PublishError("Google reports the YouTube access token as expired (expires_in=0): retry")
    return TokenInfo(scopes, expires_in, bool(creds.refresh_token))


def check_token(settings: Settings | None = None) -> TokenInfo:
    """Refresh the access token if needed, then verify scope and expiry with tokeninfo (no YouTube API call)."""
    s = settings or get_settings()
    return _tokeninfo(_credentials(s))


def status(settings: Settings | None = None) -> str:
    s = settings or get_settings()
    if not s.youtube_configured:
        return "not_configured"
    if not s.youtube_token_file.is_file():
        return "not_connected"
    from google.auth.exceptions import GoogleAuthError

    try:
        _credentials(s)  # refreshes only when the access token expired; no network call while it is valid
    except (PublishError, GoogleAuthError, ValueError):  # revoked/expired refresh token, offline, corrupt file
        return "not_connected"
    return "connected"


def describe(settings: Settings | None = None) -> dict:
    """Cheap, secret-free connection report for `factory youtube-status`: state, granted scopes, token expiry."""
    s = settings or get_settings()
    source = "file" if s.youtube_client_secret_file.is_file() else "env" if s.youtube_client_from_env else None
    out: dict = {"state": "connected", "client_source": source, "token_file": str(s.youtube_token_file)}
    if not s.youtube_configured:
        out.update(state="not_configured", error=_missing_client_message(s))
    elif not s.youtube_token_file.is_file():
        out.update(state="not_connected", error=f"YouTube channel not connected: run {CONNECT_CMD}")
    else:
        try:
            out.update(check_token(s).as_dict())
        except PublishError as e:  # NotConfigured (revoked, wrong scope) or a transient network failure
            out.update(state="not_connected", error=str(e))
    return out


# ------------------------------------------------------------------ consent flow
def _consent(flow, port: int, open_browser: bool, timeout_seconds: int):
    """Run the browser consent and map every failure of the flow to a retryable message."""
    from google_auth_oauthlib.flow import WSGITimeoutError
    from oauthlib.oauth2.rfc6749.errors import AccessDeniedError, OAuth2Error

    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)  # the library prints the URL line without flushing
    print(CONSENT_BANNER, flush=True)
    print(flush=True)
    try:
        return flow.run_local_server(
            port=port,
            open_browser=open_browser,
            timeout_seconds=timeout_seconds,
            authorization_prompt_message=AUTH_PROMPT,
            success_message=AUTH_SUCCESS,
            access_type="offline",
            prompt="consent",
        )
    except WSGITimeoutError as e:
        raise PublishError(
            f"timed out after {timeout_seconds}s waiting for the Google authorization callback "
            f"(consent not finished, or the browser could not reach http://localhost): run {CONNECT_CMD} again "
            "and complete the consent in the browser"
        ) from e
    except AccessDeniedError as e:
        raise PublishError(
            "authorization denied or cancelled in the browser (access_denied): run "
            f"{CONNECT_CMD} again and click Continue / Allow. If Google says access is blocked, add this Google "
            "account under OAuth consent screen > Test users"
        ) from e
    except OAuth2Error as e:
        detail = f"{e.error}: {e.description}" if e.description else e.error
        raise PublishError(
            f"Google authorization callback failed ({detail}): run {CONNECT_CMD} again; if it repeats with "
            "invalid_client, download the client JSON again"
        ) from e
    except requests.RequestException as e:  # token exchange over the network (must precede OSError)
        raise PublishError(
            f"network error while finishing the Google authorization ({type(e).__name__}): check the connection "
            f"and run {CONNECT_CMD} again"
        ) from None
    except OSError as e:  # e.g. cannot bind the localhost callback server
        raise PublishError(
            f"could not start the local callback server on localhost ({e}): run {CONNECT_CMD} again"
        ) from e


def connect(
    settings: Settings | None = None,
    port: int = 0,
    open_browser: bool = True,
    timeout_seconds: int = CONNECT_TIMEOUT_SECONDS,
) -> TokenInfo:
    """Run the OAuth consent in the browser once, verify the token, and persist it (mode 600).

    The token is verified with tokeninfo before it is written, so a token without the upload scope is never saved."""
    s = settings or get_settings()
    if not s.youtube_configured:
        raise NotConfigured(_missing_client_message(s))
    creds = _consent(_build_flow(s), port, open_browser, timeout_seconds)
    if not creds.refresh_token:
        raise PublishError(
            "Google did not return a refresh token: remove this app at https://myaccount.google.com/permissions "
            f"and run {CONNECT_CMD} again"
        )
    info = _tokeninfo(creds)
    _write_token(s, creds)
    return info


# ------------------------------------------------------------------ upload
def _error_reasons(e) -> set[str]:
    """Lower-cased `reason` values of an HttpError, read defensively from `error_details` and the JSON body."""
    found: set[str] = set()

    def collect(items) -> None:
        for item in items if isinstance(items, list) else [items]:
            if isinstance(item, dict) and isinstance(item.get("reason"), str):
                found.add(item["reason"].lower())

    collect(getattr(e, "error_details", None))
    try:
        body = json.loads(e.content.decode("utf-8"))
    except (ValueError, AttributeError):
        body = None
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        collect(error.get("errors"))
        collect(error.get("details"))
    return found


def _api_error(e) -> PublishError:
    """Turn a non-retryable YouTube HttpError into an actionable message."""
    reasons = _error_reasons(e)
    if reasons & _API_DISABLED_REASONS:
        return NotConfigured(
            "YouTube Data API v3 is not enabled for the Google Cloud project of this OAuth client: enable it "
            "(APIs & Services > Library > YouTube Data API v3), wait a minute, then run the command again"
        )
    if reasons & _SCOPE_REASONS:
        return NotConfigured(
            f"YouTube token has insufficient permissions (youtube.upload missing): run {CONNECT_CMD} again "
            "and leave the upload permission checked"
        )
    if reasons & _QUOTA_REASONS.keys():
        names = ", ".join(_QUOTA_REASONS[r] for r in sorted(reasons & _QUOTA_REASONS.keys()))
        return PublishError(
            f"YouTube quota or upload limit reached ({names}); not retried. "
            "The daily quota resets at midnight Pacific Time: try again later"
        )
    if e.status_code == 401:
        return NotConfigured(f"YouTube rejected the access token (401): run {CONNECT_CMD} again")
    return PublishError(f"YouTube API error {e.status_code}: {e.reason}")


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
            if e.status_code not in RETRY_STATUSES:
                raise _api_error(e) from e
            reason = f"YouTube API error {e.status_code}: {e.reason}"
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
            raise NotConfigured(_missing_client_message(self.s))

    def upload(self, req: PublishRequest) -> PublishResult:
        if req.privacy not in ("private", "unlisted"):
            raise PublishError("POC only uploads private or unlisted; switch to public manually in Studio")
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

        youtube = build("youtube", "v3", credentials=_credentials(self.s), cache_discovery=False)
        title = clean_title(req.title)
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
