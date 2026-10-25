"""TC-09 with a mock publisher + YouTube not-configured behaviour (P0-14, P0-15)."""

from __future__ import annotations

import json
import stat
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httplib2
import pytest
from googleapiclient.errors import HttpError

from factory import cli
from factory.config import get_settings
from factory.pipeline import Factory
from factory.planner import MockPlanner
from factory.publish import youtube
from factory.publish.base import MockPublisher, NotConfigured, PublishError, build_description
from factory.schema import now_iso


def fake_rendered(f: Factory) -> str:
    pid = f.create_project("Publish", 30).id
    f.plan(pid, MockPlanner())
    out = f.store.path(pid, "renders/final-short.mp4")
    out.write_bytes(b"fake mp4 bytes")

    def done(p):
        p.render.status, p.render.file, p.render.rendered_at = "done", "renders/final-short.mp4", now_iso()
        p.status = "rendered"

    f.store.update(pid, done)
    return pid


def test_tc09_mock_upload_persists_ids():
    f = Factory()
    pid = fake_rendered(f)
    pub = MockPublisher()
    p = f.publish(pid, pub, "unlisted")
    assert p.youtube_video_id == "mock0000001" and p.youtube_url is not None and p.youtube_url.endswith("mock0000001")
    assert (p.publish_status, p.publish_privacy, p.status) == ("uploaded", "unlisted", "published")
    assert p.published_at
    assert pub.uploads[0].title == f.storyboard(pid).title


def test_refuses_unrendered_and_stale():
    f = Factory()
    pid = f.create_project("No render", 30).id
    f.plan(pid, MockPlanner())
    with pytest.raises(ValueError, match="render the video"):
        f.publish(pid, MockPublisher())
    pid2 = fake_rendered(f)
    f.store.update(pid2, lambda p: setattr(p, "assets_changed_at", "9999-01-01T00:00:00+00:00"))
    with pytest.raises(ValueError, match="render again"):
        f.publish(pid2, MockPublisher())


def test_public_is_refused():
    f = Factory()
    pid = fake_rendered(f)
    with pytest.raises(Exception, match="private or unlisted"):
        f.publish(pid, MockPublisher(), "public")
    assert f.store.get(pid).publish_status == "error"


def test_youtube_not_configured():
    assert youtube.status() == "not_configured"
    with pytest.raises(NotConfigured, match="YouTube not configured"):
        youtube.YouTubePublisher()


def test_youtube_configured_but_not_connected(monkeypatch):
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "id")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "secret")
    assert youtube.status() == "not_connected"
    f = Factory()
    pid = fake_rendered(f)
    with pytest.raises(NotConfigured, match="not connected"):
        f.publish(pid, youtube.YouTubePublisher())


def test_description_has_hashtags():
    assert build_description("Hi", ["#a", "b"]).endswith("#a #b")


# ---------------------------------------------------------------- YouTube OAuth (all mocked, no network)
def client_json(kind: str = "installed") -> str:
    inner = {
        "client_id": "fake-id.apps.googleusercontent.com",
        "client_secret": "fake-client-secret",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }
    return json.dumps({kind: inner})


def write_client_file(text: str | None = None):
    path = get_settings().youtube_client_secret_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(client_json() if text is None else text)
    return path


def fake_creds():
    from google.oauth2.credentials import Credentials

    return Credentials(
        token="fake-access-token",
        refresh_token="fake-refresh-token",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="fake-id",
        client_secret="fake-client-secret",
        scopes=youtube.SCOPES,
        expiry=datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
    )


class FakeFlow:
    def __init__(self, result: object):  # what run_local_server returns, or raises when it is an Exception
        self.result = result
        self.kwargs: dict[str, object] = {}
        self.loaded: tuple[str, list[str]] | None = None

    def run_local_server(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def patch_flow(monkeypatch, flow: FakeFlow) -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow

    def from_file(path, scopes, **_kw):
        flow.loaded = ("file", list(scopes))
        return flow

    def from_config(config, scopes, **_kw):
        flow.loaded = ("env", list(scopes))
        return flow

    monkeypatch.setattr(InstalledAppFlow, "from_client_secrets_file", from_file)
    monkeypatch.setattr(InstalledAppFlow, "from_client_config", from_config)


def patch_tokeninfo(monkeypatch, scope: str = youtube.UPLOAD_SCOPE, expires_in: str = "3599") -> list:
    calls: list = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, params))
        return SimpleNamespace(status_code=200, json=lambda: {"scope": scope, "expires_in": expires_in})

    monkeypatch.setattr(youtube.requests, "get", fake_get)
    return calls


def test_client_file_is_primary_and_env_is_fallback(monkeypatch):
    assert not get_settings().youtube_configured
    write_client_file()
    assert get_settings().youtube_configured and youtube.status() == "not_connected"

    flow = FakeFlow(fake_creds())
    patch_flow(monkeypatch, flow)
    patch_tokeninfo(monkeypatch)
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "env-id")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "env-secret")
    youtube.connect()
    assert flow.loaded == ("file", [youtube.UPLOAD_SCOPE])  # the file wins over the env pair

    get_settings().youtube_client_secret_file.unlink()
    youtube.connect()
    assert flow.loaded == ("env", [youtube.UPLOAD_SCOPE])


@pytest.mark.parametrize(
    ("text", "match"),
    [
        (None, "OAuth client file not found"),
        ("{not json", "not valid JSON"),
        (client_json("web"), "found type: web"),
        (json.dumps({"installed": {"client_id": "x"}}), "missing client_secret"),
    ],
)
def test_connect_rejects_bad_client_file(text, match):
    if text is not None:
        write_client_file(text)
    with pytest.raises(NotConfigured, match=match) as exc:
        youtube.connect()
    assert "fake-client-secret" not in str(exc.value)


def test_connect_success_writes_token_with_mode_600(monkeypatch, capsys):
    write_client_file()
    flow = FakeFlow(fake_creds())
    patch_flow(monkeypatch, flow)
    calls = patch_tokeninfo(monkeypatch)
    info = youtube.connect()
    s = get_settings()
    assert s.youtube_token_file.parent == s.data_dir / "secrets"
    assert stat.S_IMODE(s.youtube_token_file.stat().st_mode) == 0o600
    assert info.scopes == (youtube.UPLOAD_SCOPE,) and info.expires_in == 3599 and info.has_refresh_token
    assert flow.kwargs["port"] == 0 and flow.kwargs["open_browser"] is True and flow.kwargs["timeout_seconds"] == 300
    assert calls == [(youtube.TOKENINFO_URL, {"access_token": "fake-access-token"})]
    out = capsys.readouterr().out
    assert out.startswith(youtube.CONSENT_BANNER) and "HUMAN ACTION REQUIRED:" in out
    assert "fake-access-token" not in out and "fake-client-secret" not in out
    assert youtube.describe(s)["state"] == "connected" and youtube.status(s) == "connected"


def test_connect_refuses_token_without_upload_scope(monkeypatch):
    write_client_file()
    patch_flow(monkeypatch, FakeFlow(fake_creds()))
    patch_tokeninfo(monkeypatch, scope="openid")
    with pytest.raises(NotConfigured, match="lacks the youtube.upload permission"):
        youtube.connect()
    assert not get_settings().youtube_token_file.exists()


@pytest.mark.parametrize(
    ("failure", "match"),
    [
        ("denied", "access_denied"),
        ("timeout", "timed out after 300s"),
        ("callback", "callback failed"),
    ],
)
def test_connect_maps_flow_failures(monkeypatch, failure, match):
    from google_auth_oauthlib.flow import WSGITimeoutError
    from oauthlib.oauth2.rfc6749.errors import AccessDeniedError, InvalidClientError

    write_client_file()
    error = {"denied": AccessDeniedError(), "timeout": WSGITimeoutError("x"), "callback": InvalidClientError()}[failure]
    patch_flow(monkeypatch, FakeFlow(error))
    with pytest.raises(PublishError, match=match):
        youtube.connect()
    assert not get_settings().youtube_token_file.exists()


def http_error(status: int, body: dict) -> HttpError:
    return HttpError(httplib2.Response({"status": status}), json.dumps(body).encode())


def api_body(reason: str | None = None, info_reason: str | None = None) -> dict:
    error: dict = {"code": 403, "message": "Forbidden", "status": "PERMISSION_DENIED"}
    if reason:
        error["errors"] = [{"reason": reason, "domain": "youtube.quota"}]
    if info_reason:
        error["details"] = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": info_reason}]
    return {"error": error}


@pytest.mark.parametrize(
    ("body", "match"),
    [
        (api_body("accessNotConfigured"), "not enabled"),
        (api_body(info_reason="SERVICE_DISABLED"), "not enabled"),
        (api_body("insufficientPermissions"), "insufficient permissions"),
        (api_body(info_reason="ACCESS_TOKEN_SCOPE_INSUFFICIENT"), "insufficient permissions"),
        (api_body("quotaExceeded"), "resets at midnight"),
        (api_body("uploadLimitExceeded"), "upload limit reached"),
    ],
)
def test_upload_maps_http_errors_and_never_retries_them(monkeypatch, body, match):
    sleeps: list[float] = []
    monkeypatch.setattr(youtube, "_sleep", sleeps.append)
    attempts: list[int] = []

    def next_chunk():
        attempts.append(1)
        raise http_error(403, body)

    with pytest.raises(PublishError, match=match):
        youtube._upload_chunks(SimpleNamespace(next_chunk=next_chunk))
    assert len(attempts) == 1 and not sleeps


def test_cli_private_test_upload_with_explicit_metadata(monkeypatch, capsys):
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "id")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "secret")
    monkeypatch.setattr(youtube, "_credentials", lambda _s: object())
    captured: dict = {}

    class FakeService:
        def videos(self):
            return self

        def insert(self, **kwargs):
            captured.update(kwargs)
            done = (None, {"id": "vid123", "status": {"privacyStatus": "private"}})
            return SimpleNamespace(next_chunk=lambda: done)

    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **k: FakeService())
    pid = fake_rendered(Factory())
    code = cli.main(["publish", pid, "--title", "Private test", "--description", "Test run"])
    assert code == 0
    snippet, status = captured["body"]["snippet"], captured["body"]["status"]
    assert snippet["title"] == "Private test" and snippet["description"].startswith("Test run")
    assert status["privacyStatus"] == "private"
    p = Factory().store.get(pid)
    assert (p.youtube_video_id, p.youtube_url) == ("vid123", "https://youtube.com/shorts/vid123")
    assert (p.publish_status, p.publish_privacy) == ("uploaded", "private") and p.published_at
    out = capsys.readouterr().out
    assert "https://youtube.com/shorts/vid123" in out and "privacy: private" in out and "video_id: vid123" in out
