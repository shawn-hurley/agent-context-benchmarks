"""Bounded controller downloads with a task-local offline/cancellation policy."""
from contextlib import contextmanager
from contextvars import ContextVar
from http.client import HTTPResponse
from pathlib import Path
import tempfile
import time
import urllib.request

_policy = ContextVar("acb_download_policy", default=(False, None))


@contextmanager
def download_policy(*, offline=False, cancelled=None):
    token = _policy.set((offline, cancelled))
    try:
        yield
    finally:
        _policy.reset(token)


def require_network(url):
    offline, cancelled = _policy.get()
    if cancelled is not None and cancelled.is_set():
        raise RuntimeError("asset preparation was cancelled")
    if offline:
        raise FileNotFoundError(f"offline: uncached asset requires download: {url}")


def open_url(url, timeout=30):
    require_network(url)
    return urllib.request.urlopen(url, timeout=timeout)


def download_file(url, destination):
    """Publish a complete download atomically; bound stalls and total duration."""
    destination = Path(destination)
    deadline = time.monotonic() + 120
    with open_url(url) as response:
        expected = response.length if isinstance(response, HTTPResponse) else None
        received = 0
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as staging:
            path = Path(staging.name)
            try:
                while True:
                    require_network(url)
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"asset download exceeded 120 seconds: {url}")
                    # HTTPResponse.read(n) can wait indefinitely for n bytes
                    # when a server trickles data below the socket timeout.
                    # read1 returns after one buffered/socket read, allowing
                    # cancellation and the overall deadline to be checked.
                    try:
                        chunk = response.read1(65536) if isinstance(response, HTTPResponse) else response.read(65536)
                    except Exception:
                        require_network(url)
                        raise
                    if not chunk:
                        break
                    staging.write(chunk)
                    received += len(chunk)
                require_network(url)
                if time.monotonic() > deadline:
                    raise TimeoutError(f"asset download exceeded 120 seconds: {url}")
                if expected is not None and received != expected:
                    raise IOError(f"incomplete asset download: expected {expected} bytes, received {received}")
                staging.flush()
                path.replace(destination)
            finally:
                path.unlink(missing_ok=True)
