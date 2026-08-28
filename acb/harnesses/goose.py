"""Goose harness adapter.

Runs Goose headless (`goose run -t <prompt> --no-session`) via `podman exec`
inside the SWE-bench eval container, pointed at the (also containerized)
proxy. The only harness with a container-mode port today -- see
`ensure_linux_binary()` below for how a Linux build gets into the image.

NOTE: verified against goose 1.46.0 (real binary, not just docs). Goose does
*not* honor the generic ANTHROPIC_BASE_URL/OPENAI_BASE_URL convention used by
other harnesses -- its provider system reads its own env vars per-provider:

* openai provider    -> OPENAI_HOST (base URL) + OPENAI_API_KEY
* anthropic provider -> ANTHROPIC_HOST (base URL) + ANTHROPIC_API_KEY

and which provider it talks to is selected via GOOSE_PROVIDER / --provider,
not inferred from which env vars are set. This adapter targets the `openai`
provider (matches locally-served OpenAI-compatible models, e.g. vLLM/Ollama,
which is what Praxis's cross-API-translation limitation restricts us to for
local runs -- see acb/proxy/praxis.py). Wiring up the `anthropic` provider
would mean setting api = "anthropic" and swapping OPENAI_HOST for
ANTHROPIC_HOST below.

Progress visibility: `--no-session` means goose persists no transcript of its
own (that's normally what its sqlite session store is for); combined with
generation often taking minutes, a plain `subprocess.run(capture_output=True)`
would leave the console silent the whole time. Instead this adapter (via the
shared plumbing in acb/harnesses/_streaming.py, also used by claude-code):

* uses `--output-format stream-json` so goose emits one JSON event per
  message/tool-call/tool-result as they happen instead of a single blob at
  the end (verified event shapes: `{"type": "message", "message": {"role":
  ..., "content": [{"type": "text"|"toolRequest"|"toolResponse", ...}]}}`
  and a final `{"type": "complete", ...}`),
* streams stdout live via Popen and writes every raw line to
  `<out_dir>/goose/<instance_id>/transcript.jsonl` as a durable record,
* prints a heartbeat roughly every HEARTBEAT_INTERVAL seconds summarizing the
  most recent activity, instead of either per-line spam or total silence.
"""

from __future__ import annotations

import shlex
import subprocess
import tarfile
import urllib.request
from pathlib import Path

from acb.container import container_cp_in, container_exec_capture
from acb.harnesses._streaming import execute
from acb.harnesses.base import HarnessAdapter, HarnessResult

# goose ships static-ish Linux release binaries here (verified against 1.46/
# 1.47); used to get goose into a SWE-bench testbed container, which has no
# package manager entry for it.
_LINUX_RELEASE_URL = (
    "https://github.com/aaif-goose/goose/releases/download/stable/"
    "goose-{arch}-unknown-linux-gnu.tar.bz2"
)
_ARCH_ALIASES = {"arm64": "aarch64", "aarch64": "aarch64", "amd64": "x86_64", "x86_64": "x86_64"}


def ensure_linux_binary(arch: str, cache_dir: Path) -> Path:
    """Download (once, cached) a Linux goose binary for ``arch``; return its path."""
    goose_arch = _ARCH_ALIASES.get(arch, arch)
    dest_dir = cache_dir / f"goose-{goose_arch}-unknown-linux-gnu"
    dest = dest_dir / "goose"
    if dest.exists():
        return dest
    dest_dir.mkdir(parents=True, exist_ok=True)
    url = _LINUX_RELEASE_URL.format(arch=goose_arch)
    print(f"[goose] downloading Linux binary for {goose_arch} from {url} ...", flush=True)
    archive_path = dest_dir / "goose.tar.bz2"
    urllib.request.urlretrieve(url, archive_path)  # noqa: S310
    with tarfile.open(archive_path) as tf:
        tf.extractall(dest_dir)  # noqa: S202
    archive_path.unlink()
    dest.chmod(0o755)
    return dest


def _describe_event(obj: dict) -> tuple[str | None, bool]:
    """Extract a short activity description from a parsed stream-json event.

    Returns (description, is_text_delta). is_text_delta is True when
    `description` is a fragment of assistant text that should be accumulated
    into a rolling preview rather than shown verbatim (goose streams text
    token-by-token, so individual fragments like " to" aren't informative on
    their own). Returns (None, False) for event shapes we don't recognize.
    """
    etype = obj.get("type")
    if etype == "complete":
        return "finalizing response", False
    if etype != "message":
        return None, False
    message = obj.get("message") or {}
    role = message.get("role")
    for item in message.get("content") or []:
        itype = item.get("type")
        if itype == "toolRequest":
            name = ((item.get("toolCall") or {}).get("value") or {}).get("name")
            return (f"running tool: {name}" if name else "running tool"), False
        if itype == "toolResponse":
            result = (item.get("toolResult") or {}).get("value") or {}
            return ("tool call failed" if result.get("isError") else "tool call finished"), False
        if itype == "text" and role == "assistant":
            return item.get("text") or "", True
    return None, False


class Goose(HarnessAdapter):
    name = "goose"
    api = "openai"  # default; runner overrides via effective_api()

    def effective_api(self, model_api: str) -> str:
        """Goose supports both openai and anthropic providers natively.

        Match the model backend's API so no proxy translation is needed:
        - model_api="openai"    -> --provider openai  + OPENAI_HOST (current default)
        - model_api="anthropic" -> --provider anthropic + ANTHROPIC_HOST
        
        NOTE: Goose's Anthropic provider (using the Anthropic Rust SDK) appears
        to be incompatible with Vertex AI's SSE streaming format, resulting in
        "empty response" errors. This is a goose/SDK issue, not a proxy issue.
        OpenAI-compatible models (vLLM, Ollama) work fine.
        """
        return model_api

    def setup_container(self, container: str, arch: str, out_dir: Path) -> None:
        """Download (once, cached) this arch's goose Linux binary and copy it
        into `container` at /usr/local/bin/goose, before run_container()
        execs it. Previously done inline in SWEBench.prepare_container() --
        moved here so benchmark code stays harness-agnostic (see
        HarnessAdapter.setup_container()); behavior is unchanged.
        """
        binary = ensure_linux_binary(arch, out_dir / ".cache")
        container_cp_in(container, binary, "/usr/local/bin/goose")
        container_exec_capture(container, ["chmod", "+x", "/usr/local/bin/goose"])

    def run_container(self, prompt: str, container: str, model: str, env: dict[str, str],
                       out_dir: Path, instance_id: str,
                       binary: str = "/usr/local/bin/goose") -> HarnessResult:
        """Exec the harness inside a running container via `podman exec`.

        `podman exec` doesn't take a Python env dict for the exec'd process
        (only for the `podman` CLI invocation itself); env vars are passed as
        repeated `-e KEY=VALUE` flags instead.
        
        Injects goose configuration to disable internet search before execution.
        """
        # Inject goose config to disable fetch extension (internet search)
        self._inject_goose_config(container)

        goose_argv = [
            binary, "run",
            "-t", prompt,
            "--provider", self.api,
            "--model", model,
            "--no-session",
            "--output-format", "stream-json",
        ]
        if system_prompt := self.config.get("system_prompt"):
            goose_argv += ["--system", system_prompt]
        if max_reps := self.config.get("max_tool_repetitions"):
            goose_argv += ["--max-tool-repetitions", str(max_reps)]

        exec_cmd = ["podman", "exec", "-i"]
        for key, value in env.items():
            exec_cmd += ["-e", f"{key}={value}"]
        # `workdir`: the directory inside the container that goose should work
        # in.  Defaults to `/testbed` (SWE-bench layout); set to `/work` for
        # ScarfBench via harness config overrides (`overrides.harness.workdir`
        # in the run config).
        #
        # `conda_env`: name of the conda env to activate before running goose.
        # SWE-bench images use a `testbed` conda env for the right Python
        # version + deps (verified: `podman exec` doesn't source ~/.bashrc
        # automatically, so the base conda Python would be used without this).
        # ScarfBench images use plain JDK+Maven with no conda -- set to None
        # or "" via `overrides.harness.conda_env: null` to skip activation.
        workdir = self.config.get("workdir", "/testbed")
        conda_env = self.config.get("conda_env", "testbed")
        inner = " ".join(shlex.quote(a) for a in goose_argv)
        if conda_env:
            preamble = (
                f"source /opt/miniconda3/etc/profile.d/conda.sh "
                f"&& conda activate {conda_env} && "
            )
        else:
            preamble = ""
        exec_cmd += [
            "--workdir", workdir, container,
            "bash", "-c", f"{preamble}exec {inner}",
        ]
        # out_dir is now the per-instance directory (instances/{test_id}/)
        transcript_path = Path(out_dir) / "transcript.jsonl"
        label = f"[goose:{instance_id}]"
        timeout = self.config.get("timeout", 1800)
        return execute(exec_cmd, env=None, cwd=None, transcript_path=transcript_path,
                       label=label, timeout=timeout, describe_event=_describe_event)

    def build_container_env(self, base_url: str, api_key: str) -> dict[str, str]:
        """Minimal env for `run_container` -- no host env inherited (irrelevant/huge).

        Goose uses per-provider env vars (OPENAI_HOST / ANTHROPIC_HOST) rather
        than the generic OPENAI_BASE_URL / ANTHROPIC_BASE_URL convention --
        verified against goose 1.46.0 (see module docstring). Branch on
        self.api, which the runner sets via effective_api() before this is called.
        """
        env = {
            "HOME": "/root",
            "GOOSE_PROVIDER": self.api,
            "GOOSE_TELEMETRY_ENABLED": "false",
            "GOOSE_MODE": "auto",
        }
        if self.api == "anthropic":
            env["ANTHROPIC_HOST"] = base_url
            env["ANTHROPIC_API_KEY"] = api_key
        else:
            env["OPENAI_HOST"] = base_url
            env["OPENAI_API_KEY"] = api_key
        return env

    @staticmethod
    def _inject_goose_config(container: str) -> None:
        """Inject goose configuration file into container to disable internet search.
        
        Copies the goose-container-config.yaml from the harness directory into the
        container at ~/.config/goose/config.yaml to ensure the fetch extension
        (internet search) is disabled.
        """
        config_src = Path(__file__).parent.parent / "goose-container-config.yaml"
        config_dst = "/root/.config/goose/config.yaml"
        
        if not config_src.exists():
            print(f"[goose] warning: config file not found at {config_src}, skipping injection",
                  flush=True)
            return
        
        try:
            # Ensure target directory exists in container
            subprocess.run(
                ["podman", "exec", "-u", "root", container, "mkdir", "-p", "/root/.config/goose"],
                check=True,
                capture_output=True,
            )
            # Copy config file into container
            subprocess.run(
                ["podman", "cp", str(config_src), f"{container}:{config_dst}"],
                check=True,
                capture_output=True,
            )
            print(f"[goose] injected config to {container}:{config_dst}", flush=True)
        except subprocess.CalledProcessError as e:
            print(f"[goose] warning: failed to inject config: {e}", flush=True)
