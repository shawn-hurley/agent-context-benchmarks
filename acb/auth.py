"""Provider authentication for controller-owned model services."""


def fetch_vertex_token() -> str:
    """Refresh Application Default Credentials without saving credential values."""
    try:
        import google.auth
        import google.auth.transport.requests
    except ImportError as exc:
        raise RuntimeError("google-auth is required for Vertex AI runs") from exc
    from google.auth.exceptions import GoogleAuthError
    try:
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"])
        credentials.refresh(google.auth.transport.requests.Request())
    except GoogleAuthError:
        # Provider exception messages may contain response bodies or credentials.
        raise RuntimeError("Cannot refresh Vertex AI credentials; configure Application Default Credentials or GOOGLE_APPLICATION_CREDENTIALS") from None
    if not credentials.token:
        raise RuntimeError("Vertex AI credentials produced no token; check Application Default Credentials")
    return credentials.token
