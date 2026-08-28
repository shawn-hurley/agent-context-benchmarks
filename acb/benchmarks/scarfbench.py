"""ScarfBench benchmark adapter.

ScarfBench (https://scarfbench.info) evaluates Java framework migration:
Jakarta EE <-> Quarkus <-> Spring.  102 focused-example apps across 6 layers,
each with 3 framework variants.

Generation follows the same container-mode pattern as SWE-bench:
  1. prepare_container() builds/starts a shared "scarfbench:latest" image
     (JDK 17 + Maven + git) in the runner's pod; copies source framework code
     (minus smoke.py/Makefile/Dockerfile) into /work; returns container name.
  2. runner.py calls Goose.setup_container() (copies goose binary in) and
     Goose.run_container() (podman exec goose in /work with workdir=/work,
     no conda activation -- set via overrides.harness in the run config).
  3. collect_prediction_container() copies /work back out to a scarf-validate-
     compatible directory structure and writes metadata.json.

Evaluation uses `scarf validate` (the hidden validation command in the scarf
CLI) as a separate grading step, analogous to SWE-bench's run_evaluation.
scarf validate copies the target framework's Makefile/Dockerfile/smoke.py
into the output dir and runs `make test`, which builds a Docker image, starts
the app, and runs pytest-based smoke tests.  The pass/fail result is read back
from the updated metadata.json.

Key differences from SWE-bench:
  - No per-instance eval image: all Java apps share the same JDK/Maven base.
  - No git diff as Prediction.model_patch: scarf validate needs the full
    migrated source tree in output/, not a patch.
  - Instances declared in config (not loaded from HF dataset): the instance
    space is small and enumerable.
  - Grading uses Docker (via the existing container_env() DOCKER_HOST +
    bin/docker Podman shim), same as SWE-bench's evaluation subprocess.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from acb.benchmarks.base import Benchmark, Instance, Prediction
from acb.container import (
    build_image,
    container_create,
    container_env,
    container_start,
    container_untar_in,
    image_exists,
)

# Files from the benchmark's source framework directory that belong to the
# build/test harness, not the application itself.  scarf eval run's own
# prepare step excludes the same set (confirmed from prepare.rs in the scarf
# CLI source).  They are NOT copied into the agent's working directory; scarf
# validate later copies the TARGET framework's equivalents in for grading.
_HARNESS_FILES = {"smoke.py", "smoke", "Makefile", "makefile", "Dockerfile", ".dockerignore"}

# Framework names the scarf validate Metadata struct accepts (snake_case, from
# the Framework enum in validate/types.rs with `serde(rename_all="snake_case")`).
_VALID_FRAMEWORKS = {"jakarta", "quarkus", "spring"}

# The Containerfile lives alongside this module's package.
_CONTAINERFILE = Path(__file__).resolve().parent.parent / "scarfbench" / "Containerfile"

_DEFAULT_IMAGE = "scarfbench:latest"


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


def _ensure_scarfbench_image(config: dict) -> str:
    """Return the scarfbench image tag, building it from the Containerfile if absent.

    Analogous to runner.py's _ensure_praxis_image(): checks for the image,
    builds once from acb/scarfbench/Containerfile if missing.
    """
    image = config.get("scarfbench_image", _DEFAULT_IMAGE)
    if image_exists(image):
        return image
    if not _CONTAINERFILE.exists():
        raise RuntimeError(
            f"{image} not found and Containerfile is missing at {_CONTAINERFILE}. "
            "Verify the acb/scarfbench/Containerfile exists in the repository."
        )
    print(f"[scarfbench] building {image} from {_CONTAINERFILE} ...", flush=True)
    build_image(_CONTAINERFILE, _CONTAINERFILE.parent, image)
    print(f"[scarfbench] built {image}", flush=True)
    return image


def _copy_source_to_container(
    source_dir: Path, container: str, container_path: str
) -> None:
    """Stage source framework code into the container at container_path.

    Copies everything from source_dir EXCEPT harness files (smoke.py, Makefile,
    Dockerfile, .dockerignore) -- these belong to the benchmark's grading harness,
    not the agent's working copy.  scarf validate copies them back in from the
    TARGET framework dir for grading.

    Uses container_untar_in() (tar pipe) rather than podman cp to avoid the
    directory-vs-contents ambiguity in podman cp semantics (see container.py).
    """
    with tempfile.TemporaryDirectory() as staging:
        staging_path = Path(staging)
        for entry in source_dir.iterdir():
            if entry.name in _HARNESS_FILES:
                continue
            dst = staging_path / entry.name
            if entry.is_dir():
                shutil.copytree(entry, dst)
            else:
                shutil.copy2(entry, dst)
        container_untar_in(container, staging_path, container_path)


def _copy_work_from_container(container: str, output_dir: Path) -> None:
    """Extract /work from container into output_dir using a tar pipe.

    podman cp container:/work/. output_dir has the same directory-vs-contents
    issue as the inbound direction, so we use `podman exec tar` to stream out.
    .git is excluded -- it's used internally during the run but is not part of
    the migrated source that scarf validate grades.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [
            "podman", "exec", container,
            "tar", "-C", "/work", "--exclude=./.git", "-c", ".",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Extract the tar stream on the host side
    import tarfile as _tarfile
    import io
    raw, stderr = proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"_copy_work_from_container: tar out of {container}:/work failed\n"
            f"stderr: {stderr.decode(errors='replace')}"
        )
    with _tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tf:
        tf.extractall(output_dir)  # noqa: S202


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
        return Path(d)

    def _scarf_eval_dir(self, output_dir: Path) -> Path:
        """Subdirectory of output_dir that holds scarf validate-compatible output."""
        return output_dir / "scarfbench-eval"

    def _agent_key(self, layer: str, app: str, source: str, target: str) -> str:
        """Folder name under scarf_eval_dir, matching scarf's EvalKey.repr() format."""
        return f"{self._AGENT_SLUG}__{layer}__{app}__{source}__{target}"

    def load_instances(self, subset=None, limit=None) -> list[Instance]:
        """Load instances from the run config's instance list.

        Each entry in config["instances"] is a dict with keys:
          layer, app, source, target

        Example:
          instances:
            - layer: business_domain
              app: cart
              source: jakarta
              target: quarkus
        """
        instances_cfg = self.config.get("instances") or []
        if not instances_cfg:
            raise RuntimeError(
                "No ScarfBench instances configured. Add an `instances:` list "
                "under scarfbench in benchmarks.yaml or overrides.benchmark in "
                "the run config."
            )
        benchmark_cache_dir = self._benchmark_cache_dir()
        instances: list[Instance] = []

        for spec in instances_cfg:
            layer = spec["layer"]
            app = spec["app"]
            source = _normalize_framework(spec["source"])
            target = _normalize_framework(spec["target"])
            instance_id = f"{layer}/{app}/{source}-to-{target}"

            if subset and instance_id not in subset:
                continue

            # Read the BDD feature spec for the app (app-level, shared across
            # frameworks).  Include it verbatim in the prompt so goose has a
            # concrete behavioral contract to preserve.
            feature_path = benchmark_cache_dir / layer / app / f"{app}.feature"
            if feature_path.exists():
                feature_content = feature_path.read_text(errors="replace")
                feature_section = (
                    f"Preserve all behavior described in this BDD specification:\n\n"
                    f"<feature>\n{feature_content}\n</feature>\n\n"
                )
            else:
                print(
                    f"[scarfbench] warning: {feature_path} not found; "
                    "omitting feature spec from prompt",
                    flush=True,
                )
                feature_section = ""

            prompt = (
                f"Migrate the Java application in /work from {source} to {target}.\n\n"
                f"{feature_section}"
                f"Follow idiomatic {target} conventions. "
                f"The source code is already in /work -- edit it in place. "
                f"Do not add or modify test files."
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

    def prepare_container(self, instance: Instance, pod: str,
                          build_dir: Path, arch: str) -> str:
        """Build/start a scarfbench container and seed /work with source code.

        Unlike SWE-bench (where the eval image already contains /testbed),
        here we:
          1. Ensure the shared scarfbench:latest base image exists.
          2. Create + start a container in the runner's pod.
          3. Copy the source framework's Java sources (minus harness files)
             into /work via a tar pipe.

        Goose's binary is NOT staged here -- Goose.setup_container() handles
        that after this method returns (same as SWE-bench).
        """
        image = _ensure_scarfbench_image(self.config)
        container_name = f"{pod}-scarfbench"
        container_create(pod, image, container_name, command=["sleep", "infinity"])
        container_start(container_name)

        layer = instance.extra["layer"]
        app = instance.extra["app"]
        source = instance.extra["source"]
        source_dir = self._benchmark_cache_dir() / layer / app / source

        if not source_dir.is_dir():
            raise RuntimeError(
                f"Source framework directory not found: {source_dir}\n"
                f"Verify benchmark_cache_dir points to the root of `scarf bench pull` output "
                f"and that the {source!r} framework directory exists for {layer}/{app}."
            )

        print(
            f"[scarfbench:{instance.instance_id}] copying {source} source "
            f"({source_dir}) into container /work ...",
            flush=True,
        )
        _copy_source_to_container(source_dir, container_name, "/work")

        # Stash out_dir on the instance so collect_prediction_container() can
        # locate the transcript and write the scarf-validate output tree.
        # build_dir is <out_dir>/image_build (set by runner.py's do_one);
        # its parent is out_dir.
        instance.extra["_out_dir"] = str(build_dir.parent)

        return container_name

    def collect_prediction_container(
        self, instance: Instance, container: str, model: str
    ) -> Prediction:
        """Extract /work and write scarf-validate-compatible output layout.

        Directory structure written under <out_dir>/scarfbench-eval/:

            <agent>__<layer>__<app>__<source>__<target>/
              run_1/
                input/        (source code snapshot -- for reference)
                output/       (agent's migrated code -- graded by scarf validate)
                validation/   (goose transcript)
                metadata.json (layer/app/framework info for scarf validate)

        The goose transcript is copied from the runner's standard transcript
        path to validation/agent.out.
        """
        layer = instance.extra["layer"]
        app = instance.extra["app"]
        source = instance.extra["source"]
        target = instance.extra["target"]

        # The runner writes to out_dir which we don't have a direct reference
        # to here -- but scarf_eval_dir is deterministic from the run's output
        # directory structure.  We store it on the instance via extra so
        # evaluate() can find it without re-deriving.  The runner passes
        # out_dir to run_container() / collect_prediction_container() only
        # via the harness; we need another channel.
        #
        # Solution: read from config["_out_dir"] which the runner sets in
        # bench_cfg (see note in evaluate()), or derive from a well-known
        # relative path.  For now we stash the result path in Prediction.output
        # and parse it back in evaluate().

        # out_dir is stashed on instance.extra["_out_dir"] by prepare_container()
        # (which receives build_dir from the runner; build_dir.parent = out_dir).
        out_dir = Path(instance.extra.get("_out_dir", "runs"))
        scarf_eval_dir = self._scarf_eval_dir(out_dir)
        agent_key = self._agent_key(layer, app, source, target)
        run_dir = scarf_eval_dir / agent_key / "run_1"
        output_dir = run_dir / "output"
        validation_dir = run_dir / "validation"
        input_dir = run_dir / "input"

        output_dir.mkdir(parents=True, exist_ok=True)
        validation_dir.mkdir(parents=True, exist_ok=True)
        input_dir.mkdir(parents=True, exist_ok=True)

        # --- Seed input/ with original source code (for reference) ---
        source_dir = self._benchmark_cache_dir() / layer / app / source
        with tempfile.TemporaryDirectory() as staging:
            staging_path = Path(staging)
            for entry in source_dir.iterdir():
                if entry.name in _HARNESS_FILES:
                    continue
                dst = staging_path / entry.name
                if entry.is_dir():
                    shutil.copytree(entry, dst)
                else:
                    shutil.copy2(entry, dst)
            # Copy staging contents to input_dir
            for item in staging_path.iterdir():
                dst = input_dir / item.name
                if item.is_dir():
                    shutil.copytree(item, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dst)

        # --- Extract /work (migrated code) from container → output/ ---
        print(
            f"[scarfbench:{instance.instance_id}] extracting /work → {output_dir} ...",
            flush=True,
        )
        _copy_work_from_container(container, output_dir)

        # --- Write metadata.json for scarf validate ---
        _write_metadata_json(
            run_dir / "metadata.json",
            agent=self._AGENT_SLUG,
            app=app,
            layer=layer,
            source_framework=source,
            target_framework=target,
            model=model,
        )

        # --- Copy goose transcript to validation/agent.out ---
        # The runner writes the transcript to <out_dir>/goose/<instance_id>/transcript.jsonl
        transcript_src = out_dir / "goose" / instance.instance_id / "transcript.jsonl"
        if transcript_src.exists():
            shutil.copy2(transcript_src, validation_dir / "agent.out")
        else:
            # Write an empty marker so scarf validate doesn't fail on a missing file
            (validation_dir / "agent.out").write_text("")
        (validation_dir / "agent.err").write_text("")

        return Prediction(
            instance_id=instance.instance_id,
            model_name_or_path=model,
            # output carries the run_dir path so evaluate() can locate it
            output=str(run_dir),
        )

    def evaluate(self, predictions: list[Prediction], run_id: str,
                 output_dir: Path) -> dict[str, bool]:
        """Grade all predictions by invoking `scarf validate`.

        scarf validate:
          1. Reads each run_N/metadata.json for (layer, app, target_framework).
          2. Copies Makefile/Dockerfile/smoke.py from benchmark's target
             framework dir into run_N/output/.
          3. Runs `make test` (Docker build → run app → pytest smoke tests).
          4. Parses run.log and writes compile_ok/deploy_ok/tests_passed back
             to metadata.json.

        We then read metadata.json to determine pass/fail:
          resolved = True  iff tests_passed > 0 and tests_passed == num_smoke_tests
          (both fields set by scarf validate; num_smoke_tests comes from the
           benchmark's own smoke test metadata).
        """
        scarf_binary = shutil.which(self.config.get("scarf_binary", "scarf"))
        if not scarf_binary:
            raise RuntimeError(
                "scarf CLI not found on PATH. "
                "Install from https://scarfbench.info/installing/ and ensure "
                "it is on PATH (or set scarfbench.scarf_binary in benchmarks.yaml)."
            )

        scarf_eval_dir = self._scarf_eval_dir(output_dir)
        benchmark_cache_dir = self._benchmark_cache_dir()

        cmd = [
            scarf_binary, "validate",
            "--conversions-dir", str(scarf_eval_dir),
            "--validations-dir", str(benchmark_cache_dir),
        ]
        if timeout_min := self.config.get("validate_timeout_minutes"):
            cmd += ["--timeout", str(int(timeout_min))]

        # Route docker CLI calls through the Podman shim (same as SWE-bench
        # evaluation subprocess -- see container_env() in acb/container.py).
        env = container_env(self.config)

        print(
            f"[scarfbench] running scarf validate for {len(predictions)} "
            f"prediction(s) ...",
            flush=True,
        )
        proc = subprocess.run(cmd, env=env)
        print(
            f"[scarfbench] scarf validate exited with code {proc.returncode}",
            flush=True,
        )

        # Read back grading results from each metadata.json
        resolved: dict[str, bool] = {}
        for pred in predictions:
            if pred.error:
                resolved[pred.instance_id] = False
                continue
            run_dir = Path(pred.output) if pred.output else None
            if run_dir is None:
                resolved[pred.instance_id] = False
                continue
            meta_path = run_dir / "metadata.json"
            if not meta_path.exists():
                print(
                    f"[scarfbench] warning: metadata.json not found at {meta_path}",
                    flush=True,
                )
                resolved[pred.instance_id] = False
                continue

            try:
                meta = json.loads(meta_path.read_text())
            except (json.JSONDecodeError, OSError) as e:
                print(
                    f"[scarfbench] warning: failed to read {meta_path}: {e}",
                    flush=True,
                )
                resolved[pred.instance_id] = False
                continue

            tests_passed = meta.get("tests_passed")
            num_smoke_tests = meta.get("num_smoke_tests")
            compile_ok = meta.get("compile_ok", "UNK")
            deploy_ok = meta.get("deploy_ok", "UNK")

            # Resolved = compiled, deployed, and all smoke tests passed.
            # If num_smoke_tests is missing (benchmark metadata.json not found
            # by scarf validate), fall back to tests_passed > 0.
            if (
                compile_ok == "TRUE"
                and deploy_ok == "TRUE"
                and tests_passed is not None
                and tests_passed > 0
                and (num_smoke_tests is None or tests_passed >= num_smoke_tests)
            ):
                resolved[pred.instance_id] = True
            else:
                resolved[pred.instance_id] = False

            print(
                f"[scarfbench]   {pred.instance_id}: "
                f"compile={compile_ok} deploy={deploy_ok} "
                f"tests={tests_passed}/{num_smoke_tests} "
                f"=> {'PASS' if resolved[pred.instance_id] else 'FAIL'}",
                flush=True,
            )

        # Ensure every prediction has an entry
        for pred in predictions:
            resolved.setdefault(pred.instance_id, False)
        return resolved
