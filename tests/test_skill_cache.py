import io
from pathlib import Path
import tarfile

import pytest

from acb.downloads import download_policy
from acb.skills.fetchers import fetch_github_release, fetch_git_repo


CONFIG = {'name': 'fixture', 'source_url': 'https://github.com/example/fixture/releases',
          'version': 'v1.0.0', 'binary_pattern': 'fixture-{version}-{arch}.tar.gz'}


def fake_download(url, destination):
    with tarfile.open(destination, 'w:gz') as archive:
        item = tarfile.TarInfo('fixture')
        data = b'fixture executable'
        item.size, item.mode = len(data), 0o755
        archive.addfile(item, io.BytesIO(data))


def test_release_cache_reuses_complete_entry_offline_and_detects_damage(tmp_path, monkeypatch):
    monkeypatch.setattr('acb.skills.fetchers._download_file', fake_download)
    directory = fetch_github_release(CONFIG, tmp_path, 'arm64')
    def unexpected(*args):
        pytest.fail('cache hit must not download')
    monkeypatch.setattr('acb.skills.fetchers._download_file', unexpected)
    with download_policy(offline=True):
        assert fetch_github_release(CONFIG, tmp_path, 'arm64') == directory
        (directory / 'fixture').write_bytes(b'changed')
        with pytest.raises(ValueError, match='contents changed'):
            fetch_github_release(CONFIG, tmp_path, 'arm64')


def test_release_source_change_is_an_offline_miss(tmp_path, monkeypatch):
    monkeypatch.setattr('acb.skills.fetchers._download_file', fake_download)
    fetch_github_release(CONFIG, tmp_path, 'arm64')
    from acb.downloads import download_file
    monkeypatch.setattr('acb.skills.fetchers._download_file', download_file)
    with download_policy(offline=True), pytest.raises(FileNotFoundError, match='offline'):
        fetch_github_release({**CONFIG, 'source_url': 'https://github.com/another/project/releases'}, tmp_path, 'arm64')
    assert not list((tmp_path / 'verified').glob('skill-*'))


def test_release_missing_binary_cannot_publish(tmp_path, monkeypatch):
    def empty(url, destination):
        with tarfile.open(destination, 'w:gz'):
            pass
    monkeypatch.setattr('acb.skills.fetchers._download_file', empty)
    with pytest.raises(ValueError, match='missing its executable'):
        fetch_github_release(CONFIG, tmp_path, 'arm64')
    assert not list((tmp_path / 'verified').glob('*/manifest.json'))


def test_failed_release_transfer_leaves_no_cache_entry(tmp_path, monkeypatch):
    def fail(url, destination):
        Path(destination).write_bytes(b'partial download')
        raise OSError('transfer failed')
    monkeypatch.setattr('acb.skills.fetchers._download_file', fail)
    with pytest.raises(OSError, match='transfer failed'):
        fetch_github_release(CONFIG, tmp_path, 'arm64')
    assert not list((tmp_path / 'verified').glob('skill-*'))
    assert not list((tmp_path / 'verified').glob('*/manifest.json'))


def test_git_cache_distinguishes_refs_and_reuses_offline(tmp_path, monkeypatch):
    calls = []
    def clone(command, **kwargs):
        calls.append(command)
        target = Path(command[-1])
        (target / 'SKILL.md').write_text('---\nname: fixture\ndescription: Fixture\n---\n')
        (target / 'reference.txt').write_text(command[command.index('--branch') + 1])
    monkeypatch.setattr('acb.skills.fetchers.subprocess.run', clone)
    config = {'name': 'fixture', 'source_url': 'https://example.invalid/repo', 'ref': 'first'}
    first = fetch_git_repo(config, tmp_path)
    second = fetch_git_repo({**config, 'ref': 'second'}, tmp_path)
    assert first != second
    assert (first / 'reference.txt').read_text() == 'first'
    assert (second / 'reference.txt').read_text() == 'second'
    with download_policy(offline=True):
        assert fetch_git_repo(config, tmp_path) == first
        with pytest.raises(FileNotFoundError, match='offline'):
            fetch_git_repo({**config, 'ref': 'third'}, tmp_path)
    assert len(calls) == 2


def test_real_git_cache_concurrent_fetch_and_offline_reuse(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import subprocess
    repo = tmp_path / 'repository'
    repo.mkdir()
    run = subprocess.run
    run(['git', 'init', '--initial-branch=fixture', str(repo)], check=True, capture_output=True)
    (repo / 'SKILL.md').write_text('---\nname: fixture\ndescription: Fixture\n---\nRead reference.txt.\n')
    (repo / 'reference.txt').write_text('real git fixture')
    run(['git', '-C', str(repo), 'add', '.'], check=True, capture_output=True)
    run(['git', '-C', str(repo), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
         '-c', 'commit.gpgsign=false', 'commit', '-m', 'fixture'], check=True, capture_output=True)
    clones = []
    def observe(command, **kwargs):
        if command[:2] == ['git', 'clone']:
            clones.append(command)
        return run(command, **kwargs)
    monkeypatch.setattr('acb.skills.fetchers.subprocess.run', observe)
    config = {'name': 'fixture', 'source_url': str(repo), 'ref': 'fixture'}
    cache = tmp_path / 'cache'
    with ThreadPoolExecutor(max_workers=2) as pool:
        directories = list(pool.map(lambda _: fetch_git_repo(config, cache), range(2)))
    assert directories[0] == directories[1]
    assert len(clones) == 1
    assert (directories[0] / 'reference.txt').read_text() == 'real git fixture'
    with download_policy(offline=True):
        assert fetch_git_repo(config, cache) == directories[0]
    assert len(clones) == 1
    assert not list((cache / 'verified').glob('skill-*'))
