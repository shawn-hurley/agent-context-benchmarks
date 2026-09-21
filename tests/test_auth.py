from types import SimpleNamespace

import pytest
import google.auth
from google.auth.exceptions import DefaultCredentialsError, RefreshError

from acb.auth import fetch_vertex_token


def test_vertex_token_is_refreshed_without_persisting_credentials(monkeypatch, tmp_path):
    calls = []
    credentials = SimpleNamespace(token="fresh-secret", refresh=lambda request: calls.append(request))
    monkeypatch.setattr(google.auth, "default", lambda **kwargs: (credentials, "project"))
    monkeypatch.chdir(tmp_path)
    assert fetch_vertex_token() == "fresh-secret"
    assert len(calls) == 1
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("failure", [DefaultCredentialsError, RefreshError])
def test_vertex_errors_explain_setup_without_leaking_provider_response(monkeypatch, failure):
    def fail(**kwargs):
        raise failure("private provider response and credentials")
    monkeypatch.setattr(google.auth, "default", fail)
    with pytest.raises(RuntimeError, match="Application Default Credentials") as caught:
        fetch_vertex_token()
    assert "private" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_vertex_empty_token_is_an_error(monkeypatch):
    credentials = SimpleNamespace(token=None, refresh=lambda request: None)
    monkeypatch.setattr(google.auth, "default", lambda **kwargs: (credentials, "project"))
    with pytest.raises(RuntimeError, match="no token"):
        fetch_vertex_token()
