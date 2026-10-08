"""Portable named starter configurations, including an installed setup task."""
from importlib.resources import files

import yaml

TEMPLATES = ("quickstart", "rh-swe-bench")


def starter_files(template, environment):
    run = {"schema_version": 2, "config_dir": "config", "skills": [], "extensions": []}
    machine = {"environment": environment}
    if template == "quickstart":
        run.update(run_id="smoke", benchmark="smoke", harness="goose", model="quickstart-model",
                   subset=["smoke"], execution={"max_workers": 1, "timeout": 300}, output_dir="runs/quickstart")
        models = {"quickstart-model": {"model": "YOUR_MODEL_ID", "api": "openai", "endpoint": "api.openai.com:443",
                                       "tls": True, "key_env": "OPENAI_API_KEY"}}
        machine["cache_dir"] = "../.cache"
    elif template == "rh-swe-bench":
        run.update(run_id="rh-swe-bench-smoke", benchmark="rh-swe-bench",
                   harness=["goose", "pi", "opencode", "claude-code"], model="local-model",
                   subset=["task-0000"], execution={"max_workers": 1})
        models = {"local-model": {"model": "YOUR_MODEL_ID", "api": "openai",
                                  "endpoint": "host.containers.internal:8000", "tls": False}}
    else:
        raise ValueError(f"unknown starter template: {template}")
    documents = {"run.yaml": run, "config/models.yaml": models, "config/machine.yaml": machine}
    if template == "quickstart":
        documents["config/benchmarks.yaml"] = {"smoke": {"path": "../tasks",
                                                        "reward_metric": "reward", "success_value": 1, "attempts": 1}}
    result = {path: yaml.safe_dump(document, sort_keys=False).encode() for path, document in documents.items()}
    if template == "quickstart":
        def collect(root, prefix):
            for item in root.iterdir():
                path = f"{prefix}/{item.name}"
                if item.is_dir():
                    collect(item, path)
                else:
                    result[path] = item.read_bytes()
        collect(files("acb").joinpath("assets/starters/quickstart/tasks"), "tasks")
    return result
