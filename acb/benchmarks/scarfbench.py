"""ScarfBench discovery, migration prompts, and native grading.

Harbor exports source projects and collects complete generated projects.
This module preserves scarf validate and make test grading, including the
compatibility harness for published bundles without a Makefile."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from acb.ui import ProgressTracker

from acb.benchmarks.base import Benchmark, Instance, Prediction
from acb.logging_config import log_debug
from acb.container import container_env
from acb.utils import normalize_instance_id_for_path

# Files from the benchmark's source framework directory that belong to the
# build/test harness, not the application itself. ScarfBench's prepare step
# excludes these. It does include source test.sh, which gives the agent useful
# context about the original observable interface. The validator replaces it
# with the target framework's test.sh before grading.
_HARNESS_FILES = {
    "smoke.py", "smoke", "Makefile", "makefile", "Dockerfile",
    ".dockerignore",
}

# Framework names the scarf validate Metadata struct accepts (snake_case, from
# the Framework enum in validate/types.rs with `serde(rename_all="snake_case")`).
_VALID_FRAMEWORKS = {"jakarta", "quarkus", "spring"}

# The Containerfile lives alongside this module's package.
_CONTAINERFILE = Path(__file__).resolve().parent.parent / "scarfbench" / "Containerfile"
_PAIR_PROMPTS_DIR = _CONTAINERFILE.parent / "prompts"

_DEFAULT_IMAGE = "scarfbench:latest"


def _benchmark_task_text(benchmark_dir: Path, layer: str, app: str) -> tuple[str, str] | None:
    """Read an explicit app-level task description when a bundle supplies one.

    Current v0.1.2 pulls contain none of these files. Keeping the lookup
    allows a future or custom benchmark bundle to supply task text without
    silently replacing it with our generic migration instructions.
    """
    app_dir = benchmark_dir / layer / app
    for name in ("prompt.txt", "prompt.md", "task.txt", "task.md", f"{app}.feature"):
        path = app_dir / name
        if path.is_file():
            content = path.read_text(errors="replace").strip()
            if content:
                return name, content
    return None


def _migration_prompt(benchmark_dir: Path, *, layer: str, app: str,
                      source: str, target: str) -> str:
    """Build an agent-defined prompt from benchmark task text and source files.

    ScarfBench's CLI supplies framework names and source code, but no prompt.
    Pair guidance is adapted from ScarfBench's public example agent skills,
    without their logging and reference-file requirements, which ACB does not
    stage in the agent workspace.
    """
    task = _benchmark_task_text(benchmark_dir, layer, app)
    pair = f"{source}-to-{target}"
    pair_guidance = (_PAIR_PROMPTS_DIR / f"{pair}.md").read_text().strip()
    task_section = (
        f"Benchmark task description ({task[0]}):\n<task>\n{task[1]}\n</task>\n\n"
        if task else ""
    )
    return (
        f"Migrate the Java application in /work from {source} to {target}.\n\n"
        f"{task_section}"
        f"Migration guidance for {pair}:\n{pair_guidance}\n\n"
        "Preserve the source application's observable behavior, including its "
        "HTTP paths, protocol, data behavior, and relevant configuration. "
        "Inspect the source code and any README or test.sh in /work to identify "
        "that behavior. The source test.sh checks the source runtime, so its "
        "URL and port are not necessarily the target validation contract. "
        "Follow idiomatic target-framework conventions while "
        "updating dependencies, build plugins, configuration, and application code. "
        "Package the application, fix build failures, and check that it starts "
        "and responds as intended where the available tools permit. "
        "The benchmark will validate with its target-framework Dockerfile and "
        "test script. Do not add or modify test files."
    )


def _normalize_framework(name: str) -> str:
    """Normalize user-supplied framework aliases to the canonical snake_case name.

    scarf validate's Framework enum only accepts "jakarta", "quarkus", "spring".
    Common aliases (e.g. "springboot", "spring-boot") are mapped here so users
    can write either form in the run config.
    """
    n = name.lower().strip()
    aliases = {
        "springboot": "spring",
        "spring-boot": "spring",
        "spring_boot": "spring",
        "jakartaee": "jakarta",
        "jakarta-ee": "jakarta",
        "jakarta_ee": "jakarta",
    }
    n = aliases.get(n, n)
    if n not in _VALID_FRAMEWORKS:
        raise ValueError(
            f"unknown ScarfBench framework {name!r}; must be one of "
            f"{sorted(_VALID_FRAMEWORKS)} (or a recognized alias)"
        )
    return n


def _write_metadata_json(path: Path, *, agent: str, app: str, layer: str,
                         source_framework: str, target_framework: str,
                         model: str, repeat: int = 1) -> None:
    """Write a metadata.json compatible with scarf validate's Metadata struct.

    Field types match validate/types.rs exactly:
      - status: SCREAMING_SNAKE_CASE ("CONVERTED")
      - source_framework / target_framework: snake_case ("jakarta","quarkus","spring")
      - compile_ok / deploy_ok: UPPERCASE TriState ("UNK" until scarf validate fills them)
    """
    metadata = {
        "status": "CONVERTED",
        "agent": agent,
        "app": app,
        "layer": layer,
        "repeat": repeat,
        "source_framework": source_framework,
        "target_framework": target_framework,
        "compile_ok": "UNK",
        "deploy_ok": "UNK",
        "solution_name": "acb",
        "model": model,
    }
    path.write_text(json.dumps(metadata, indent=2))


def _ensure_validation_harness(benchmark_dir: Path, *, layer: str, app: str,
                                framework: str) -> None:
    """Fill the validator files omitted by ScarfBench v0.1.2 benchmark pulls.

    The released benchmark contains ``Dockerfile`` and ``test.sh`` but the
    validator contract expects a ``Makefile`` and target-framework
    ``metadata.json``.  Generate those two small compatibility files only
    when they are absent; official harness files always win.
    """
    framework_dir = benchmark_dir / layer / app / framework
    test_script = framework_dir / "test.sh"
    makefile = framework_dir / "Makefile"
    if not test_script.exists():
        if makefile.exists():
            return
        raise FileNotFoundError(
            f"ScarfBench target has neither Makefile nor test.sh: {framework_dir}"
        )

    existing_makefile = makefile.read_text() if makefile.exists() else ""
    legacy_makefile = (
        (existing_makefile.startswith("IMAGE_NAME ?= scarfbench-")
         and "docker exec -e BASE_URL=" in existing_makefile)
        or (existing_makefile.startswith("# Generated by ACB for ScarfBench v0.1.2's missing Makefile.\n")
            and "--iidfile" not in existing_makefile)
    )
    if not makefile.exists() or legacy_makefile:
        # The pulled release supplies the test itself but not the `make test`
        # entrypoint required by scarf validate. Run that script unchanged: its
        # default URL, port and assertions belong to the benchmark, not us.
        # Replace only compatibility Makefiles from older ACB runs; an upstream
        # Makefile always takes precedence.
        # Each conversion has its own output directory. A stable directory
        # checksum keeps concurrent validations from sharing a container name.
        container = (
            f"scarfbench-{app}-{framework}-validation-"
            "$(shell printf '%s' '$(CURDIR)' | cksum | cut -d' ' -f1)"
        )
        makefile.write_text(
            "# Generated by ACB for ScarfBench v0.1.2's missing Makefile.\n"
            "IMAGE_ID_FILE := .acb-validation-image-id\n"
            f"CONTAINER_NAME ?= {container}\n\n"
            ".PHONY: build up down test\n\n"
            "build:\n"
            "\trm -f $(IMAGE_ID_FILE)\n"
            "\tdocker build --iidfile $(IMAGE_ID_FILE) .\n"
            "\t@echo BUILD SUCCESS\n\n"
            "up: build\n"
            "\t-docker rm -f $(CONTAINER_NAME) >/dev/null 2>&1\n"
            "\tdocker run -d --name $(CONTAINER_NAME) $$(cat $(IMAGE_ID_FILE))\n\n"
            "down:\n"
            "\t-docker rm -f $(CONTAINER_NAME) >/dev/null 2>&1\n\n"
            "test: up\n"
            "\t@status=1; trap '$(MAKE) down >/dev/null 2>&1' EXIT; "
            "for i in $$(seq 1 90); do "
            "if docker exec $(CONTAINER_NAME) bash /app/test.sh; "
            "then status=0; break; fi; "
            "if ! docker exec $(CONTAINER_NAME) true >/dev/null 2>&1; then "
            "echo 'Application container exited'; docker logs $(CONTAINER_NAME); "
            "break; fi; "
            "sleep 1; done; "
            "if [ $$status -eq 0 ]; then "
            "echo 'Application started and ready.'; "
            "echo '===== 1 passed ====='; fi; "
            "exit $$status\n"
        )

    smoke_metadata = framework_dir / "metadata.json"
    if not smoke_metadata.exists():
        # test.sh is a single aggregate health check, so it represents one
        # smoke test for ScarfBench's leaderboard metadata.
        smoke_metadata.write_text(json.dumps({"num_smoke_tests": 1}, indent=2))


class ScarfBench(Benchmark):
    """ScarfBench adapter -- Java framework migration benchmark.

    Instances are declared in the run config (overrides.benchmark.instances)
    rather than loaded from a dataset: the instance space is small and
    enumerable (layer/app/source-framework/target-framework tuples).
    """

    name = "scarfbench"

    # Directory name used as the "agent" component in scarf validate's
    # expected output structure: <agent>__<layer>__<app>__<source>__<target>/
    _AGENT_SLUG = "acb"

    def _benchmark_cache_dir(self) -> Path:
        d = self.config.get("benchmark_cache_dir")
        if not d:
            raise RuntimeError(
                "scarfbench.benchmark_cache_dir is not set. "
                "Run `scarf bench pull --dest <dir>` and set the path in "
                "config/benchmarks.yaml or overrides.benchmark.benchmark_cache_dir."
            )
        return Path(d).expanduser()

    def _scarf_eval_dir(self, output_dir: Path) -> Path:
        """Subdirectory of output_dir that holds scarf validate-compatible output."""
        return output_dir / "scarfbench-eval"

    def _agent_key(self, layer: str, app: str, source: str, target: str) -> str:
        """Folder name under scarf_eval_dir, matching scarf's EvalKey.repr() format."""
        return f"{self._AGENT_SLUG}__{layer}__{app}__{source}__{target}"

    def _run_dir_for_instance(self, output_dir: Path, instance_id: str) -> Path:
        """Locate the conversion tree written by collect_prediction_container()."""
        layer, app, conversion = instance_id.split("/", 2)
        source, target = conversion.split("-to-", 1)
        return self._scarf_eval_dir(output_dir) / self._agent_key(
            layer, app, source, target
        ) / "run_1"

    def load_instances(self, subset=None, limit=None) -> list[Instance]:
        """Load explicit migrations, or discover apps for the configured pair.

        Each optional entry in config["instances"] is a dict with keys:
          layer, app, source, target
        With no instances, config["source"] and config["target"] select the
        directed pair. Omitting both retains all-pair discovery for callers
        that instantiate ScarfBench without the example benchmark registry.

        Example:
          instances:
            - layer: business_domain
              app: cart
              source: jakarta
              target: quarkus
        """
        benchmark_cache_dir = self._benchmark_cache_dir()
        instances_cfg = self.config.get("instances")
        if not instances_cfg:
            configured_source = self.config.get("source")
            configured_target = self.config.get("target")
            if (configured_source is None) != (configured_target is None):
                raise ValueError("ScarfBench source and target must be configured together")
            if configured_source is not None:
                configured_source = _normalize_framework(configured_source)
                configured_target = _normalize_framework(configured_target)
                if configured_source == configured_target:
                    raise ValueError("ScarfBench source and target must differ")
            if not benchmark_cache_dir.is_dir():
                raise RuntimeError(
                    f"ScarfBench benchmark directory not found: {benchmark_cache_dir}. "
                    "Run `scarf bench pull` and set benchmark_cache_dir."
                )
            apps: list[tuple[str, str, set[str]]] = []
            # Order by conversion first so a small limit covers different apps.
            # This is deterministic across filesystems and harnesses.
            for layer_dir in sorted(benchmark_cache_dir.iterdir()):
                if not layer_dir.is_dir():
                    continue
                for app_dir in sorted(layer_dir.iterdir()):
                    if app_dir.is_dir():
                        frameworks = {
                            framework for framework in _VALID_FRAMEWORKS
                            if (app_dir / framework).is_dir()
                        }
                        if len(frameworks) >= 2:
                            apps.append((layer_dir.name, app_dir.name, frameworks))
            if not apps:
                raise RuntimeError(
                    f"No ScarfBench framework pairs found in {benchmark_cache_dir}. "
                    "Check benchmark_cache_dir or run `scarf bench pull`."
                )
            instances_cfg = []
            sources = [configured_source] if configured_source else sorted(_VALID_FRAMEWORKS)
            for source in sources:
                targets = [configured_target] if configured_target else sorted(_VALID_FRAMEWORKS - {source})
                for target in targets:
                    for layer, app, frameworks in apps:
                        if source in frameworks and target in frameworks:
                            instances_cfg.append({
                                "layer": layer,
                                "app": app,
                                "source": source,
                                "target": target,
                            })
            if not instances_cfg:
                raise RuntimeError(
                    f"No ScarfBench apps found for {configured_source}-to-"
                    f"{configured_target} in {benchmark_cache_dir}"
                )
        instances: list[Instance] = []

        for spec in instances_cfg:
            layer = spec["layer"]
            app = spec["app"]
            source = _normalize_framework(spec["source"])
            target = _normalize_framework(spec["target"])
            instance_id = f"{layer}/{app}/{source}-to-{target}"

            if subset and instance_id not in subset:
                continue

            prompt = _migration_prompt(
                benchmark_cache_dir, layer=layer, app=app,
                source=source, target=target,
            )

            instances.append(Instance(
                instance_id=instance_id,
                prompt=prompt,
                extra={
                    "layer": layer,
                    "app": app,
                    "source": source,
                    "target": target,
                },
            ))
            if limit and len(instances) >= limit:
                break

        return instances


    def evaluate(
        self,
        predictions: list[Prediction] | None,
        run_id: str,
        output_dir,
        tracker: ProgressTracker | None = None,
        instance_id: str | None = None,
        tracker_key: str | None = None,
    ) -> dict[str, bool]:
        """Grade predictions by invoking `scarf validate`.

        scarf validate:
          1. Reads each run_N/metadata.json for (layer, app, target_framework).
          2. Copies the target framework's Dockerfile and test harness into
             run_N/output/.
          3. Runs `make test` (Docker build → run app → target test.sh for
             v0.1.2's compatibility Makefile).
          4. Parses run.log and writes compile_ok/deploy_ok/tests_passed back
             to metadata.json.

        We then read metadata.json to determine pass/fail:
          resolved = True  iff tests_passed > 0 and tests_passed == num_smoke_tests
          (both fields set by scarf validate; num_smoke_tests comes from the
           benchmark's own smoke test metadata).
        
        Args:
            predictions: Legacy parameter (ignored, reads from disk)
            run_id: Unique identifier for this run
            output_dir: Harness output directory containing instances/
            tracker: Optional tracker for status updates
            instance_id: If set, only evaluate this instance
            tracker_key: Composite key {harness}-{instance_id} for tracker updates
        
        Returns:
            Dictionary mapping instance_id to resolved status
        """
        output_dir = Path(output_dir)
        
        # Collect predictions to evaluate
        preds_to_eval = []
        
        if instance_id:
            # Per-instance mode: evaluate only this instance
            pred_file = output_dir / "instances" / normalize_instance_id_for_path(instance_id) / "prediction.json"
            if pred_file.exists():
                pred_data = json.loads(pred_file.read_text())
                preds_to_eval.append(Prediction(
                    instance_id=pred_data["instance_id"],
                    model_name_or_path=pred_data["model_name_or_path"],
                    model_patch=pred_data.get("model_patch"),
                    output=str(self._run_dir_for_instance(output_dir, instance_id)),
                    error=None,
                ))
        else:
            # Batch mode: aggregate all predictions from instances/
            instances_dir = output_dir / "instances"
            if instances_dir.exists():
                for inst_dir in sorted(instances_dir.iterdir()):
                    if inst_dir.is_dir():
                        pred_file = inst_dir / "prediction.json"
                        if pred_file.exists():
                            pred_data = json.loads(pred_file.read_text())
                            preds_to_eval.append(Prediction(
                                instance_id=pred_data["instance_id"],
                                model_name_or_path=pred_data["model_name_or_path"],
                                model_patch=pred_data.get("model_patch"),
                                output=str(self._run_dir_for_instance(output_dir, pred_data["instance_id"])),
                                error=None,
                            ))
            elif predictions:
                # Fallback: use provided predictions
                preds_to_eval = predictions
        
        if not preds_to_eval:
            return {instance_id: False} if instance_id else {}
        
        # Note: predictions are stored in instances/*/prediction.json (source of truth)
        # HTML report reads directly from instance directories, no separate predictions.jsonl needed
        
        # Update tracker: mark verification starting
        if tracker and tracker_key:
            tracker.start_verification(tracker_key)
        
        scarf_binary = shutil.which(self.config.get("scarf_binary", "scarf"))
        if not scarf_binary:
            raise RuntimeError(
                "scarf CLI not found on PATH. "
                "Install from https://scarfbench.info/installing/ and ensure "
                "it is on PATH (or set scarfbench.scarf_binary in benchmarks.yaml)."
            )

        scarf_eval_dir = self._scarf_eval_dir(output_dir)
        benchmark_cache_dir = self._benchmark_cache_dir()

        # ScarfBench v0.1.2's published benchmark tree contains test.sh but
        # omits the Makefile and smoke-test metadata expected by `scarf
        # validate`. Materialize compatibility files before validation;
        # official Makefiles take precedence.
        for pred in preds_to_eval:
            run_meta = Path(pred.output) / "metadata.json" if pred.output else None
            if run_meta and run_meta.exists():
                run_metadata = json.loads(run_meta.read_text())
                _ensure_validation_harness(
                    benchmark_cache_dir,
                    layer=run_metadata["layer"],
                    app=run_metadata["app"],
                    framework=run_metadata["target_framework"],
                )
                # `scarf validate` copies Makefile/Dockerfile/metadata.json
                # itself, but not test.sh.  Stage the target framework's
                # script explicitly; otherwise a source-framework script can
                # probe the wrong port (e.g. Jakarta 9080 vs Quarkus 8080).
                target_test = (
                    benchmark_cache_dir / run_metadata["layer"] /
                    run_metadata["app"] / run_metadata["target_framework"] /
                    "test.sh"
                )
                if target_test.exists():
                    output_tree = run_meta.parent / "output"
                    output_tree.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target_test, output_tree / "test.sh")

        cmd = [
            scarf_binary, "validate",
            "--conversions-dir", str(scarf_eval_dir),
            "--validations-dir", str(benchmark_cache_dir),
        ]
        if timeout_min := self.config.get("validate_timeout_minutes"):
            cmd += ["--timeout", str(int(timeout_min))]

        # Route docker CLI calls through the Podman shim
        env = container_env(self.config)
        
        # Prevent TTY detection to avoid Docker/Podman progress bars and status messages
        # from bypassing stdout/stderr redirection and appearing on the terminal.
        # This ensures evaluation output stays in the log file and doesn't interfere
        # with the Rich Live display. (See acb/container.py:119-122 for same pattern)
        env["TERM"] = "dumb"
        env.setdefault("DOCKER_BUILDKIT", "0")

        if os.environ.get("ACB_DEBUG_UI"):
            log_debug(
                f"running scarf validate for {len(preds_to_eval)} "
                f"prediction(s) ..."
            )
        
        # Redirect evaluation subprocess output to log file to prevent terminal interference
        # This prevents scarf validate's output from bypassing Rich's Live display
        eval_log_id = normalize_instance_id_for_path(instance_id) if instance_id else "batch"
        eval_log_path = output_dir / f"scarfbench_eval_{eval_log_id}.log"
        try:
            with eval_log_path.open("w") as eval_log:
                proc = subprocess.run(
                    cmd,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=eval_log,
                    stderr=subprocess.STDOUT
                )
            if os.environ.get("ACB_DEBUG_UI"):
                log_debug(
                    f"scarf validate exited with code {proc.returncode}"
                )
        except Exception as e:
            error_msg = f"scarf validate failed: {str(e)}"
            if tracker and tracker_key:
                tracker.complete_verification(tracker_key, False, error=error_msg)
            raise

        # Read back grading results from each metadata.json
        resolved: dict[str, bool] = {}
        for pred in preds_to_eval:
            if pred.error:
                resolved[pred.instance_id] = False
                continue
            
            run_dir = Path(pred.output) if pred.output else None
            if run_dir is None:
                resolved[pred.instance_id] = False
                continue
            
            meta_path = run_dir / "metadata.json"
            if not meta_path.exists():
                if os.environ.get("ACB_DEBUG_UI"):
                    log_debug(
                        f"warning: metadata.json not found at {meta_path}"
                    )
                resolved[pred.instance_id] = False
                continue

            try:
                meta = json.loads(meta_path.read_text())
            except (json.JSONDecodeError, OSError) as e:
                if os.environ.get("ACB_DEBUG_UI"):
                    log_debug(
                        f"warning: failed to read {meta_path}: {e}"
                    )
                resolved[pred.instance_id] = False
                continue

            tests_passed = meta.get("tests_passed")
            num_smoke_tests = meta.get("num_smoke_tests")
            compile_ok = meta.get("compile_ok", "UNK")
            deploy_ok = meta.get("deploy_ok", "UNK")

            # scarf validate can count a success marker echoed in a Makefile
            # command even when make test ultimately fails. The run log records
            # make's nonzero result, which must take precedence over metadata.
            run_log = run_dir / "validation" / "run.log"
            make_failed = run_log.exists() and re.search(
                r"(?m)^make(?:\[\d+\])?: \*\*\*", run_log.read_text(errors="replace")
            ) is not None

            # Resolved = compiled, deployed, and all smoke tests passed.
            if (
                not make_failed
                and compile_ok == "TRUE"
                and deploy_ok == "TRUE"
                and tests_passed is not None
                and tests_passed > 0
                and (num_smoke_tests is None or tests_passed >= num_smoke_tests)
            ):
                resolved[pred.instance_id] = True
            else:
                resolved[pred.instance_id] = False

            if os.environ.get("ACB_DEBUG_UI"):
                log_debug(
                    f"{pred.instance_id}: "
                    f"compile={compile_ok} deploy={deploy_ok} "
                    f"tests={tests_passed}/{num_smoke_tests} "
                    f"=> {'PASS' if resolved[pred.instance_id] else 'FAIL'}"
                )

        # Ensure every prediction has an entry
        for pred in preds_to_eval:
            resolved.setdefault(pred.instance_id, False)
        
        # Update tracker with results
        if tracker and tracker_key and instance_id and instance_id in resolved:
            tracker.complete_verification(tracker_key, resolved[instance_id])
        
        return resolved
