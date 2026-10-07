"""Check a built wheel's installed imports, runtime assets and offline export."""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


SMOKE = r'''
import importlib
import importlib.util
from pathlib import Path
import sys

installed = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(installed))
for name in ("acb.cli", "acb.cli_output", "acb.starters", "acb.task_discovery", "acb.auth", "acb.proxy.metrics", "acb.harbor.worker",
             "acb.harbor.benchmark_verifier", "acb.report_bundle", "acb.maven_cache"):
    module = importlib.import_module(name)
    assert Path(module.__file__).resolve().is_relative_to(installed), name
for name in ("acb.runner", "acb.integrations.legacy", "acb.integrations.runtime",
             "acb.proxy.recording"):
    assert importlib.util.find_spec(name) is None, name
root = installed / "acb"
for pattern in ("catalog/*.yaml", "scarfbench/Containerfile", "scarfbench/prompts/*.md",
                "assets/praxis/Containerfile", "assets/praxis/*/Cargo.toml",
                "assets/praxis/*/Cargo.lock", "assets/praxis/*/src/*.rs",
                "integrations/assets/rtk/*.py", "integrations/assets/rtk/*.ts",
                "integrations/assets/rtk/*.mjs", "integrations/assets/caveman/*.py",
                "integrations/assets/caveman/*.ts", "integrations/assets/caveman/Containerfile",
                "harnesses/assets/*.json", "catalog/caveman/*.md",
                "report_assets/*.js", "report_assets/*.md",
                "assets/starters/quickstart/tasks/smoke/task.toml",
                "assets/starters/quickstart/tasks/smoke/environment/Dockerfile",
                "assets/starters/quickstart/tasks/smoke/solution/solve.sh",
                "assets/starters/quickstart/tasks/smoke/tests/test.sh"):
    assert list(root.glob(pattern)), pattern
from acb.workflows import load_workflow
from acb.harbor.skills import task_skills
for name in ("kantra-rgctl", "migiq"):
    workflow = load_workflow(name, Path.cwd(), "scarfbench", ["goose"])
    skill_root = Path(workflow["source_dir"]) / "skills"
    skills, records = task_skills(skill_root)
    assert "rgctl" in {item["name"] for item in skills}
    assert (Path(workflow["source_dir"]) / "Cargo.lock").is_file()
from acb.html_components import render_page, Section
page = render_page("Offline report", [Section("<canvas id=\"chart\"></canvas>", {"chart": {"type": "bar", "data": {"labels": [], "datasets": []}}})])
assert "cdn.jsdelivr.net" not in page
assert "Chart" in page
from acb.harbor.dataset import prepare_dataset, verify_manifest
work = Path.cwd()
from acb.cli import main
from acb.config import RunConfig
from acb.resolver import resolve
from acb.task_discovery import discover_tasks
starter = work / "starter"
main(["init", str(starter), "--template", "quickstart", "--environment", "docker"])
starter_plan = resolve(RunConfig.from_file(starter / "run.yaml")).to_dict()
assert starter_plan["environment"] == "docker"
assert discover_tasks(starter_plan)["tasks"] == [{"id": "smoke", "selected": True}]
setup_manifest = prepare_dataset(starter_plan)
verify_manifest(setup_manifest)
assert setup_manifest["tasks"][0]["oracle"]
bundle = work / "bundle"
for framework in ("jakarta", "quarkus"):
    app = bundle / "business_domain/cart" / framework
    app.mkdir(parents=True)
    (app / "Main.java").write_text(framework)
    (app / "test.sh").write_text("exit 0\n")
plan = {"benchmark": "scarfbench", "benchmark_config": {
    "benchmark_cache_dir": str(bundle), "source": "jakarta", "target": "quarkus"},
    "cache_dir": str(work / "cache"), "offline": True, "subset": None, "limit": None}
manifest = prepare_dataset(plan)
verify_manifest(manifest)
assert len(manifest["tasks"]) == 1
print("Installed package imports, resources and offline ScarfBench export passed.")
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    wheel = args.wheel.resolve(strict=True)
    with zipfile.ZipFile(wheel) as archive:
        retired = {"acb/runner.py", "acb/integrations/legacy.py",
                   "acb/integrations/runtime.py", "acb/proxy/recording.py"}
        assert not retired.intersection(archive.namelist()), "wheel contains retired modules"
    with tempfile.TemporaryDirectory(prefix="acb-package-check-") as temporary:
        root = Path(temporary)
        installed = root / "installed"
        subprocess.run(["uv", "pip", "install", "--no-deps", "--target", str(installed),
                        str(wheel)], check=True, cwd=root)
        subprocess.run([sys.executable, "-I", "-c", SMOKE, str(installed)],
                       check=True, cwd=root)


if __name__ == "__main__":
    main()
