# ScarfBench migration prompt examples

These six directed-pair prompts are adapted from the public [ScarfBench example agent skills](https://github.com/scarfbench/scarfbench-evals/tree/main/agents/codex-with-skills/skills). They are ACB agent instructions, not benchmark task text. The ScarfBench CLI leaves prompts and skills to each agent implementation.

The example skills require additional reference files and extensive migration logs. ACB does not stage those files, so these prompts retain the migration guidance without those requirements. `acb.benchmarks.scarfbench` inserts the selected pair prompt into each instance's prompt; app-level task text, if a benchmark bundle supplies it, is included separately.
