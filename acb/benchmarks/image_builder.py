"""On-demand SWE-bench instance image builder.

SWE-bench's own evaluation harness only checks a local image cache, then falls
back to pulling from a registry (``create_container()`` in
``swebench/harness/run_evaluation.py``) -- it never builds from source. The
published images are also amd64-only (baked in as ``FROM --platform=linux/amd64``
in each task's Dockerfile), which doesn't run natively on Apple Silicon.

This module builds a single instance's image on demand, from the same source
SWE-bench's own image-building CLI uses -- the task repo
(``SWE-bench/swe-bench-tasks``, one directory per instance with its own
Dockerfile) -- via plain ``podman build`` (no ``docker``/``buildx`` binary
needed; confirmed against this vendored harness's own ``docker_build.py``,
which shells out to a literal ``docker buildx build`` and would not work here).

Building natively for arm64 requires patching each task's Dockerfile (verified
by hand against ``psf__requests-1142`` and confirmed working end-to-end
against its real ``eval.sh``/``gold.patch``/``test.patch``):

* ``FROM --platform=linux/amd64 ...`` is a hard per-stage platform pin that
  overrides the build's own ``--platform`` flag -- it has to be stripped, not
  just overridden.
* The Miniconda installer URL is architecture-specific
  (``Linux-x86_64.sh`` -> ``Linux-aarch64.sh``).
* The embedded ``environment.yml``'s exact conda pins (e.g.
  ``ld_impl_linux-64=2.40=h12ee557_0``) are x86_64-only -- some by build hash,
  some (``ld_impl_linux-64``, ``libgcc-ng``, ...) by package *name*. These
  don't exist in the ``linux-aarch64`` channel index at all. They're relaxed
  to name(+major version) pins and left for conda's solver to resolve
  natively; this trades exact transitive-dependency fidelity with the
  official x86_64 image for an image that actually builds. For dependency-
  heavy repos (astropy/scikit-learn/matplotlib-era instances with old pinned
  C-extension builds) this relaxation may not be enough -- some exact pins
  may have no aarch64 build at all, which would need per-instance attention.
"""

from __future__ import annotations

import re
import urllib.request
from pathlib import Path

from acb.container import build_image, image_exists, image_tag

TASK_REPO = "SWE-bench/swe-bench-tasks"

# Package names that are architecture-coded (not just build-hash-coded) in
# conda's `defaults`/`main` channel -- these simply don't exist under
# `linux-aarch64`, so they're dropped rather than relaxed. Conda pulls in
# whatever aarch64-native equivalents are needed transitively via `python=...`.
_ARCH_CODED_PACKAGES = {
    "_libgcc_mutex", "_openmp_mutex", "ld_impl_linux-64",
    "libgcc-ng", "libgomp", "libstdcxx-ng",
}

_PIN_LINE_RE = re.compile(r"^(\s*-\s*)([A-Za-z0-9_.\-]+)=([^=\s]+)(?:=\S+)?\s*$")


def image_name(instance_id: str, arch: str) -> str:
    """Match SWE-bench's own naming (`ImageSpec.name` in image_spec.py)."""
    arch_tag = "x86_64" if arch in ("amd64", "x86_64") else arch
    safe_id = instance_id.replace("__", "_1776_")
    return f"sweb.eval.{arch_tag}.{safe_id}:latest".lower()


def platform_for(arch: str) -> str:
    if arch in ("amd64", "x86_64"):
        return "linux/amd64"
    if arch == "arm64":
        return "linux/arm64/v8"
    raise ValueError(f"unsupported arch {arch!r}")


def fetch_dockerfile(instance_id: str, task_repo: str = TASK_REPO, ref: str = "main") -> str:
    url = f"https://raw.githubusercontent.com/{task_repo}/{ref}/tasks/{instance_id}/Dockerfile"
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
        return resp.read().decode("utf-8")


def _relax_environment_yml(dockerfile: str) -> str:
    """Relax the embedded environment.yml's exact conda pins for arm64.

    Operates line-by-line inside the heredoc block SWE-bench's task-repo
    Dockerfiles embed (`cat <<'EOF_...' > /root/environment.yml`); leaves
    everything outside a `- name=version=build` dependency line untouched.
    """
    out_lines = []
    for line in dockerfile.splitlines():
        m = _PIN_LINE_RE.match(line)
        if not m:
            out_lines.append(line)
            continue
        prefix, pkg, version = m.group(1), m.group(2), m.group(3)
        if pkg in _ARCH_CODED_PACKAGES:
            continue  # drop; conda resolves an aarch64-native equivalent transitively
        out_lines.append(f"{prefix}{pkg}={version}")
    return "\n".join(out_lines) + ("\n" if dockerfile.endswith("\n") else "")


def patch_dockerfile_for_arch(dockerfile: str, arch: str) -> str:
    if arch in ("amd64", "x86_64"):
        return dockerfile  # published Dockerfiles are already amd64-native
    if arch != "arm64":
        raise ValueError(f"unsupported arch {arch!r}")

    patched = re.sub(r"FROM\s+--platform=\S+\s+", "FROM ", dockerfile)
    patched = patched.replace("-Linux-x86_64.sh", "-Linux-aarch64.sh")
    patched = _relax_environment_yml(patched)
    return patched


def ensure_instance_image(
    instance_id: str,
    arch: str,
    build_dir: Path,
    task_repo: str = TASK_REPO,
    force_rebuild: bool = False,
    eval_alias: str | None = None,
) -> str:
    """Return the local image name for ``instance_id``, building it if needed.

    ``eval_alias``, if given, is the exact image name SWE-bench's evaluation
    will look for (the dataset row's own ``image`` field --
    ``SWEBench.load_instances()`` captures it into ``Instance.extra["image"]``).
    Evaluation reads that name directly with no override mechanism and
    always names the *published* (amd64/x86_64) image, which was never built
    for -- and doesn't exist under -- our local arch tag. Tagging our local
    image under that exact alias makes evaluation's own local-cache check
    (``client.images.get()`` in ``run_evaluation.py``) hit directly, instead
    of falling through to a pull of an image that was never published for
    this architecture (and which crashes outright on a Podman-only machine
    with no `docker-credential-desktop` -- see ``container_env()``).
    """
    name = image_name(instance_id, arch)
    if not force_rebuild and image_exists(name):
        if eval_alias and not image_exists(eval_alias):
            image_tag(name, eval_alias)
        return name

    print(f"[image] building {name} (arch={arch}) ...", flush=True)
    dockerfile = fetch_dockerfile(instance_id, task_repo)
    dockerfile = patch_dockerfile_for_arch(dockerfile, arch)

    context_dir = build_dir / instance_id
    context_dir.mkdir(parents=True, exist_ok=True)
    dockerfile_path = context_dir / "Dockerfile"
    dockerfile_path.write_text(dockerfile)

    build_image(dockerfile_path, context_dir, name, platform=platform_for(arch))
    print(f"[image] built {name}", flush=True)
    if eval_alias:
        image_tag(name, eval_alias)
    return name
