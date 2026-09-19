"""Task-owned language environment requirements, verified before inference."""
import json
import re
import shlex


RH_REVISION = "a31c36f6cb6d2c9f3caea36745564b3f4958239c"


def language_profile(source, revision, metadata):
    profile = metadata.get("acb_runtime")
    if profile is None:
        if source == "rounakbende/rh-swe-bench" and revision == RH_REVISION:
            return {"conda_env": "testbed", "source": "rh-swe-bench-pinned-profile"}
        return {"conda_env": None, "source": "image-path"}
    if not isinstance(profile, dict) or set(profile) - {"conda_env"}:
        raise ValueError("metadata.acb_runtime supports only conda_env")
    name = profile.get("conda_env")
    if name is not None and (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name)):
        raise ValueError("metadata.acb_runtime.conda_env must be an environment name or null")
    return {"conda_env": name, "source": "task-metadata"}


def apply_language_profile(config, profile):
    name = profile["conda_env"]
    if "conda_env" in config and config["conda_env"] != name:
        raise ValueError("harness conda_env conflicts with the task runtime profile")
    config["conda_env"] = name


async def inspect_language_environment(environment, profile):
    """Verify activation and freeze the actual interpreter and prefix."""
    name = profile["conda_env"]
    if name is None:
        return {**profile, "python_executable": None, "prefix": None}
    script = (
        "source /opt/miniconda3/etc/profile.d/conda.sh && conda activate "
        + shlex.quote(name) + " && python -c " + shlex.quote(
            "import json,sys; print(json.dumps({'python_executable':sys.executable,'prefix':sys.prefix}))"
        )
    )
    result = await environment.exec("bash -c " + shlex.quote(script), timeout_sec=30)
    if result.return_code:
        raise ValueError(f"task requires conda environment {name!r}, but activation failed")
    try:
        record = json.loads(result.stdout)
        if set(record) != {"python_executable", "prefix"} or any(
            not isinstance(value, str) or not value.startswith("/") for value in record.values()
        ):
            raise ValueError("invalid interpreter record")
    except (ValueError, TypeError) as error:
        raise ValueError("task language environment probe returned invalid evidence") from error
    return {**profile, **record}
