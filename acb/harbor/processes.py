"""Cancellation-safe Compose subprocesses for the pinned Harbor provider."""
import asyncio
import os
import signal

from harbor.environments.docker.docker import _sanitize_docker_compose_project_name


async def stop_process_group(process):
    """Reap the Compose client and its engine subprocesses, even if it exited."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(), 5)
    except asyncio.TimeoutError:
        pass
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    await asyncio.wait_for(process.wait(), 5)


async def compose_command(environment, command, *, check=True, timeout_sec=None,
                          stdin_data=None, on_output=None):
    # Harbor 0.23's buffered collector does not handle cancellation. Isolate
    # the local client process group so a cancelled cp cannot outlive teardown.
    runtime = environment.runtime()
    argv = [*runtime.compose, '--project-name', _sanitize_docker_compose_project_name(environment.session_id)]
    if runtime.supports_compose_project_directory:
        argv += ['--project-directory', str(environment.environment_dir.resolve())]
    for path in environment._docker_compose_paths:
        argv += ['-f', str(path.resolve())]
    argv += command
    process = await asyncio.create_subprocess_exec(
        *argv, env=environment._compose_env_vars(include_os_env=True),
        cwd=str(environment.environment_dir.resolve()) if environment.environment_dir.is_dir() else None,
        stdin=asyncio.subprocess.PIPE if stdin_data is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        if on_output is not None:
            result = await environment._collect_streamed_output(process, timeout_sec=timeout_sec,
                stdin_data=stdin_data, on_output=on_output)
        else:
            result = await environment._collect_buffered_output(process, timeout_sec=timeout_sec, stdin_data=stdin_data)
    except BaseException as error:
        try:
            await stop_process_group(process)
        except Exception as cleanup_error:
            error.add_note(f"Compose process cleanup failed: {cleanup_error}")
        raise
    if check and result.return_code:
        raise RuntimeError(f"Compose {command[0]} failed ({result.return_code}): {result.stdout or result.stderr}")
    return result
