import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile

import pytest

from acb.harnesses import opencode_runtime


@pytest.mark.parametrize("corrupt", [False, True])
def test_locked_runtime_integrity_publication_and_cache(monkeypatch, tmp_path, corrupt):
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        data = b'{"name":"@opencode-ai/plugin","version":"1.18.22"}'
        member = tarfile.TarInfo("package/package.json")
        member.size = len(data)
        tar.addfile(member, io.BytesIO(data))
    data = archive.getvalue()
    integrity = "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()
    lock = {"packages": {"": {"dependencies": {"@opencode-ai/plugin": "1.18.22"}},
                         "node_modules/@opencode-ai/plugin": {"version": "1.18.22", "integrity": integrity,
                             "resolved": "https://registry.npmjs.org/@opencode-ai/plugin/-/plugin-1.18.22.tgz"},
                         "node_modules/unused-native-accelerator": {"optional": True}}}
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "opencode-plugin-runtime-1.18.22.lock.json").write_text(json.dumps(lock))
    monkeypatch.setattr(opencode_runtime, "files", lambda *args: tmp_path)
    calls = []
    def download(*args, **kwargs):
        calls.append(args)
        return io.BytesIO(b"corrupt" if corrupt else data)
    monkeypatch.setattr(opencode_runtime.urllib.request, "urlopen", download)
    cache = tmp_path / "cache"
    if corrupt:
        with pytest.raises(ValueError, match="integrity mismatch"):
            opencode_runtime.ensure_plugin_runtime(cache)
        assert not list(cache.glob("*/acb-runtime-manifest.json"))
    else:
        runtime = opencode_runtime.ensure_plugin_runtime(cache)
        assert opencode_runtime.ensure_plugin_runtime(cache) == runtime
        assert len(calls) == 1
        assert (runtime / "node_modules/@opencode-ai/plugin/package.json").is_file()
        assert not (runtime / "node_modules/unused-native-accelerator").exists()
        assert json.loads((runtime / "package-lock.json").read_text()) == lock


def test_opencode_mcp_uses_native_schema():
    from acb.mcp import MCPServerManager
    manager = MCPServerManager()
    servers = [{"name": "fixture", "command": "python", "args": ["fixture.py"], "env": {"TEST": "yes"}}]
    assert manager.generate_config(servers, "opencode") == {
        "mcp": {"fixture": {"type": "local", "command": ["python", "fixture.py"], "environment": {"TEST": "yes"}}}
    }
    assert "mcpServers" in manager.generate_config(servers, "pi")
