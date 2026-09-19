from io import BytesIO
import threading

import pytest

from acb.downloads import download_file, download_policy


def test_offline_cache_miss_never_opens_network(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('network was opened')
    monkeypatch.setattr('urllib.request.urlopen', forbidden)
    with download_policy(offline=True), pytest.raises(FileNotFoundError, match='offline'):
        download_file('https://example.invalid/asset', tmp_path / 'asset')
    assert not list(tmp_path.iterdir())


def test_cancelled_download_preserves_destination(tmp_path, monkeypatch):
    cancelled = threading.Event()
    class Response(BytesIO):
        def read(self, length):
            cancelled.set()
            return super().read(length)
    monkeypatch.setattr('urllib.request.urlopen', lambda *args, **kwargs: Response(b'partial'))
    target = tmp_path / 'asset'
    target.write_bytes(b'original')
    with download_policy(cancelled=cancelled), pytest.raises(RuntimeError, match='cancelled'):
        download_file('https://example.invalid/asset', target)
    assert target.read_bytes() == b'original'
    assert list(tmp_path.iterdir()) == [target]


def test_download_publishes_complete_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr('urllib.request.urlopen', lambda *args, **kwargs: BytesIO(b'complete'))
    target = tmp_path / 'asset'
    download_file('https://example.invalid/asset', target)
    assert target.read_bytes() == b'complete'


def test_cancellation_at_eof_does_not_publish(tmp_path, monkeypatch):
    cancelled = threading.Event()
    class Response(BytesIO):
        def read(self, length):
            value = super().read(length)
            if not value:
                cancelled.set()
            return value
    monkeypatch.setattr('urllib.request.urlopen', lambda *args, **kwargs: Response(b'complete'))
    target = tmp_path / 'asset'
    with download_policy(cancelled=cancelled), pytest.raises(RuntimeError, match='cancelled'):
        download_file('https://example.invalid/asset', target)
    assert not list(tmp_path.iterdir())
