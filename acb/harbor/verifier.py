"""Preparation of separate verifiers at the pinned Harbor 0.23 boundary."""
from harbor.models.task.verifier_mode import resolve_effective_verifier_env_config
from harbor.trial.trial import Trial


async def inspect_verifiers(job, output):
    contracts = {}
    for config in job._trial_configs:
        # Keep inspection artifacts separate from agent trial accounting.
        config = config.model_copy(update={'trials_dir': output / 'verifier-inspection'})
        trial = await Trial.create(config)
        try:
            steps = trial.task.config.steps or [None]
            for step in steps:
                environment_config = resolve_effective_verifier_env_config(trial.task.config, step)
                if environment_config is None:
                    continue
                policy = trial._network_plan(step, env_config=environment_config)
                async with trial._separate_verifier_env(
                    environment_config, key=step.name if step else 'trial', plan=policy, step_cfg=step,
                ) as environment:
                    key = environment._verifier_key
                    record = environment._observed_verifier_contract
                    if key in contracts and contracts[key] != record:
                        raise ValueError('separate verifier contract changed during inspection')
                    contracts[key] = record
        finally:
            await trial._close_bridge()
            trial._close_logger_handler()
    return contracts
