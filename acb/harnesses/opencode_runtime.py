"""Stage OpenCode's mandatory plugin API dependencies before offline generation.

OpenCode waits for a background npm install when external plugins are configured.
Use verified npm tarballs and a bundled lock, without npm or install scripts.
Optional native accelerators are omitted; the mandatory dependency tree is portable.
"""
from __future__ import annotations

import base64
import hashlib
from importlib.resources import files
import io
import json
from pathlib import Path
import tarfile
import tempfile
import urllib.request
from acb.downloads import open_url

from ._cache import binary_cache_lock

VERSION = "1.18.22"


def ensure_plugin_runtime(cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    lock_bytes = files("acb.harnesses").joinpath("assets", f"opencode-plugin-runtime-{VERSION}.lock.json").read_bytes()
    digest = hashlib.sha256(lock_bytes).hexdigest()
    key = f"opencode-plugin-runtime-{VERSION}-{digest[:16]}"
    destination = cache_dir / key
    with binary_cache_lock(cache_dir, key):
        if (destination / "acb-runtime-manifest.json").is_file():
            return destination
        lock = json.loads(lock_bytes)
        with tempfile.TemporaryDirectory(dir=cache_dir, prefix=key + "-") as temp:
            root = Path(temp) / "runtime"
            root.mkdir()
            installed = {}
            for name, package in lock["packages"].items():
                if not name or package.get("optional"):
                    continue
                target = root / name
                if not name.startswith("node_modules/") or not target.resolve().is_relative_to(root.resolve()):
                    raise ValueError("invalid locked OpenCode dependency path")
                url = package["resolved"]
                integrity = package["integrity"]
                if not url.startswith("https://registry.npmjs.org/") or not integrity.startswith("sha512-"):
                    raise ValueError("OpenCode runtime requires pinned npm tarballs with SHA-512 integrity")
                with open_url(url, timeout=30) as response:
                    data = response.read()
                actual = "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()
                if actual != integrity:
                    raise ValueError(f"OpenCode runtime integrity mismatch: {name}")
                with tempfile.TemporaryDirectory(dir=temp) as unpack:
                    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
                        archive.extractall(unpack, filter="data")
                    source = Path(unpack) / "package"
                    if json.loads((source / "package.json").read_text())["version"] != package["version"]:
                        raise ValueError(f"OpenCode runtime version mismatch: {name}")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source.rename(target)
                installed[name] = {"version": package["version"], "integrity": integrity}
            (root / "package.json").write_text(json.dumps(lock["packages"][""]) + "\n")
            (root / "package-lock.json").write_bytes(lock_bytes)
            (root / "acb-runtime-manifest.json").write_text(json.dumps({"version": VERSION, "lock_sha256": digest,
                "packages": installed, "optional_dependencies": "omitted", "install_scripts": "disabled"}, indent=2) + "\n")
            # Publish only a complete runtime; incomplete downloads never become cache hits.
            root.rename(destination)
    return destination
