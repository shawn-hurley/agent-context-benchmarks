"""Full-screen editor for a single run, with a lossless path for existing YAML."""
from __future__ import annotations

import curses
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from importlib.resources import files
import os
from pathlib import Path
import re
import tempfile

import yaml

from acb.config import Registries, RunConfig
from acb.resolver import HARNESSES, ResolvedPlan, defaults, resolve


FORM_KEYS = ("run_id", "benchmark", "harness", "model", "workflow", "subset", "limit",
             "skills", "extensions", "timeout", "max_workers")
UI_ERRORS = (OSError, ValueError, KeyError, TypeError, yaml.YAMLError)
NO_WORKFLOW = "No workflow"
CUSTOM_WORKFLOW = "Enter workflow directory..."


def bundled_workflows() -> list[str]:
    """Names accepted by the resolver's bundled-workflow lookup."""
    return sorted(item.name for item in files("acb.workflows").iterdir()
                  if item.is_dir() and item.joinpath("workflow.yaml").is_file())


@dataclass
class RunDraft:
    config: RunConfig
    changed: set[str] = field(default_factory=set)
    source: Path | None = None
    source_text: str | None = None

    @classmethod
    def create(cls, config_dir: str | None = None) -> "RunDraft":
        registry = Registries.load(Path(config_dir) if config_dir else None)
        catalog = defaults()
        models = {**registry.proxy.get("models", {}), **registry.models}
        cfg = RunConfig(
            run_id="run", benchmark=next(iter({**catalog["benchmarks"], **registry.benchmarks})),
            harness="goose", model=next(iter(models), ""), skills=[], extensions=[],
            config_dir=str(Path(config_dir).expanduser().absolute()) if config_dir else None,
        )
        return cls(cfg)

    @classmethod
    def load(cls, path: str | Path, config_dir: str | None = None) -> "RunDraft":
        source = Path(path).expanduser().resolve()
        cfg = RunConfig.from_file(source)
        changed = set()
        if config_dir:
            cfg.config_dir = str(Path(config_dir).expanduser().absolute())
            changed.add("config_dir")
        return cls(cfg, changed=changed, source=source,
                   source_text=source.read_text(encoding="utf-8"))

    def value(self, key: str):
        if key == "timeout":
            return self.config.execution.get("timeout")
        if key == "max_workers":
            return self.config.execution.get("max_workers", self.config.max_workers)
        return getattr(self.config, key)

    def set(self, key: str, value) -> None:
        if key not in FORM_KEYS:
            raise KeyError(key)
        if key == "timeout":
            self.config.execution = {**self.config.execution}
            if value is None:
                self.config.execution.pop("timeout", None)
            else:
                self.config.execution["timeout"] = value
        elif key == "max_workers" and "max_workers" in self.config.execution:
            self.config.execution = {**self.config.execution, "max_workers": value}
        else:
            setattr(self.config, key, value)
        self.changed.add(key)

    def candidate(self, *, save_path: Path | None = None, unsaved: bool = False) -> RunConfig:
        cfg = deepcopy(self.config)
        if save_path:
            cfg.source_file = str(save_path.absolute())
        if unsaved:
            cfg.interactive_run = "unsaved"
        return cfg


def _last_line(node) -> int:
    """Last line with YAML syntax, excluding detached trailing comments."""
    if isinstance(node, yaml.MappingNode):
        return max((_last_line(value) for _, value in node.value), default=node.start_mark.line)
    if isinstance(node, yaml.SequenceNode):
        return max((_last_line(value) for value in node.value), default=node.start_mark.line)
    return node.start_mark.line


def _dump_field(key: str, value) -> list[str]:
    return yaml.safe_dump({key: value}, sort_keys=False, allow_unicode=True).splitlines(keepends=True)


def _scalar_line(indent: int, key: str, value) -> str:
    return " " * indent + f"{key}: " + yaml.safe_dump(value).splitlines()[0] + "\n"


def _patched_yaml(text: str, changes: dict[str, object]) -> str:
    """Replace only edited YAML nodes; keep unrelated fields and comments verbatim."""
    node = yaml.compose(text)
    if node is not None and not isinstance(node, yaml.MappingNode):
        raise ValueError("run YAML must contain a mapping")
    entries = {key.value: (key, value) for key, value in node.value} if node else {}
    lines = text.splitlines(keepends=True)
    edits: list[tuple[int, int, list[str]]] = []
    appended: list[str] = []
    changes = dict(changes)
    execution = entries.get("execution")
    if execution and isinstance(execution[1], yaml.MappingNode) and execution[1].flow_style:
        existing = {key.value for key, _ in execution[1].value}
        nested = {key for key in ("timeout", "max_workers") if key in changes and
                  (key == "timeout" or key in existing)}
        if nested:
            value_map = yaml.safe_load(text)["execution"]
            for key in nested:
                value = changes.pop(key)
                if value is None:
                    value_map.pop(key, None)
                else:
                    value_map[key] = value
            edits.append((execution[0].start_mark.line, _last_line(execution[1]) + 1,
                          _dump_field("execution", value_map)))
    for key, value in changes.items():
        if key == "timeout":
            execution = entries.get("execution")
            if execution is None:
                if value is not None:
                    appended.extend(_dump_field("execution", {"timeout": value}))
                continue
            parent_key, parent = execution
            if not isinstance(parent, yaml.MappingNode):
                raise ValueError("execution must be a mapping to edit timeout")
            child = next(((k, v) for k, v in parent.value if k.value == "timeout"), None)
            if child:
                start = child[0].start_mark.line
                end = _last_line(child[1]) + 1
                replacement = [] if value is None else [_scalar_line(child[0].start_mark.column, "timeout", value)]
                edits.append((start, end, replacement))
            elif value is not None:
                if parent.value:
                    indent = parent.value[0][0].start_mark.column
                    edits.append((_last_line(parent) + 1, _last_line(parent) + 1,
                                  [_scalar_line(indent, "timeout", value)]))
                else:
                    edits.append((parent_key.start_mark.line, _last_line(parent) + 1,
                                  _dump_field("execution", {"timeout": value})))
            continue
        if key == "max_workers" and "execution" in entries:
            parent = entries["execution"][1]
            if isinstance(parent, yaml.MappingNode):
                child = next(((k, v) for k, v in parent.value if k.value == "max_workers"), None)
                if child:
                    edits.append((child[0].start_mark.line, _last_line(child[1]) + 1,
                                  [_scalar_line(child[0].start_mark.column, "max_workers", value)]))
                    continue
        replacement = [] if value is None else _dump_field(key, value)
        if key in entries:
            key_node, value_node = entries[key]
            edits.append((key_node.start_mark.line, _last_line(value_node) + 1, replacement))
        elif replacement:
            appended.extend(replacement)
    for start, end, replacement in sorted(edits, reverse=True):
        lines[start:end] = replacement
    if appended:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.extend(appended)
    return "".join(lines)


def _save(draft: RunDraft, path: Path) -> None:
    path = path.expanduser().absolute()
    if path.exists() and path != draft.source:
        raise ValueError(f"{path} already exists; load it first to edit it")
    if draft.source is not None:
        current = draft.source.read_text(encoding="utf-8")
        if current != draft.source_text:
            raise ValueError(f"{draft.source} changed while the editor was open; load it again")
        changes = {key: draft.value(key) for key in draft.changed}
        content = _patched_yaml(current, changes)
    else:
        cfg = draft.candidate(save_path=path)
        data = asdict(cfg)
        for key in ("source_file", "interactive_run"):
            data.pop(key, None)
        data = {key: value for key, value in data.items() if value is not None}
        content = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    parsed = yaml.safe_load(content)
    if not isinstance(parsed, dict):
        raise ValueError("edited run YAML must contain a mapping")
    restored = RunDraft(RunConfig(**parsed))
    for key in FORM_KEYS:
        if restored.value(key) != draft.value(key):
            raise ValueError(f"edited run YAML did not retain {key}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as temp:
        temp.write(content)
        temp_path = Path(temp.name)
    try:
        if path.exists():
            os.chmod(temp_path, path.stat().st_mode)
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def finish(draft: RunDraft, choice: str, path: str | Path | None = None) -> ResolvedPlan | None:
    """Validate screen values, then commit a save choice before returning a plan."""
    if choice == "cancel":
        return None
    if choice == "unsaved":
        return resolve(draft.candidate(unsaved=True))
    if choice != "save" or path is None:
        raise ValueError("save requires a YAML path")
    destination = Path(path).expanduser().absolute()
    if destination.suffix not in {".yaml", ".yml"}:
        raise ValueError("save path must end in .yaml or .yml")
    plan = resolve(draft.candidate(save_path=destination))
    _save(draft, destination)
    return plan


class _Screen:
    def __init__(self, window, config_dir: str | None):
        self.window = window
        self.config_dir = config_dir
        self.error = ""

    def draw(self, title: str, rows: list[str], footer: str = "↑↓ move  Enter select  Esc back"):
        self.window.erase()
        height, width = self.window.getmaxyx()
        for y, line in enumerate([title, "", *rows, "", self.error, footer]):
            if y >= height:
                break
            try:
                self.window.addnstr(y, 0, line, max(0, width - 1))
            except curses.error:
                pass
        self.window.refresh()

    def menu(self, title: str, options: list[str], selected: set[str] | None = None,
             details: list[str] | None = None):
        position = 0
        multi = selected is not None
        selected = set(selected or ())
        while True:
            rows = [("> " if index == position else "  ") +
                    (("[x] " if option in selected else "[ ] ") if multi and option != "Done" else "") + option
                    for index, option in enumerate(options)]
            self.draw(title, [*(details or []), *( [""] if details else []), *rows],
                      "↑↓ move  Space toggle  Enter select  Esc back" if multi else "↑↓ move  Enter select  Esc back")
            key = self.window.getch()
            if key in (27,):
                return None
            if key in (curses.KEY_UP, ord("k")):
                position = (position - 1) % len(options)
            elif key in (curses.KEY_DOWN, ord("j")):
                position = (position + 1) % len(options)
            elif multi and key == ord(" ") and options[position] != "Done":
                name = options[position]
                selected.symmetric_difference_update({name})
            elif key in (10, 13, curses.KEY_ENTER):
                if multi:
                    if options[position] == "Done":
                        return [name for name in options if name in selected]
                    selected.symmetric_difference_update({options[position]})
                else:
                    return options[position]

    def prompt(self, title: str, current: str = "") -> str | None:
        value = list(current)
        try:
            curses.curs_set(1)
        except curses.error:
            pass
        try:
            while True:
                height, width = self.window.getmaxyx()
                shown = "".join(value)[-max(1, width - 2):]
                self.draw(title, ["Enter a value, or leave blank to clear:", shown],
                          "Enter accept  Esc cancel  Backspace delete")
                self.window.move(min(3, height - 1), min(len(shown), width - 2))
                key = self.window.get_wch()
                if key == "\x1b":
                    return None
                if key in ("\n", "\r", curses.KEY_ENTER):
                    return "".join(value).strip()
                if key in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                    if value:
                        value.pop()
                elif isinstance(key, str) and key.isprintable():
                    value.append(key)
        finally:
            try:
                curses.curs_set(0)
            except curses.error:
                pass

    def choices(self, draft: RunDraft, key: str) -> list[str]:
        registry = Registries.load(draft.config.registry_dir())
        catalog = defaults()
        if key == "benchmark":
            return list(dict.fromkeys([*catalog["benchmarks"], *registry.benchmarks]))
        if key == "harness":
            return list(HARNESSES)
        if key == "model":
            return list(dict.fromkeys([*registry.models, *registry.proxy.get("models", {})]))
        selected = draft.value(key) or []
        current = [item if isinstance(item, str) else item.get("name") for item in selected]
        return list(dict.fromkeys([*catalog[key], *getattr(registry, key),
                                   *(name for name in current if isinstance(name, str))]))

    def edit(self, draft: RunDraft, key: str):
        current = draft.value(key)
        if key == "workflow":
            bundled = bundled_workflows()
            options = [NO_WORKFLOW, *bundled]
            if current and current not in bundled:
                options.append(current)
            options.append(CUSTOM_WORKFLOW)
            result = self.menu("Select workflow", options,
                               details=["Choose a bundled workflow or enter a directory path.",
                                        "Custom paths are relative to the run YAML, or current directory if unsaved."])
            if result == CUSTOM_WORKFLOW:
                value = self.prompt("Workflow directory (relative to run YAML or current directory)", current or "")
                if value is None:
                    return
                if not value:
                    self.error = "Enter a workflow directory or choose No workflow"
                    return
                draft.set(key, value)
            elif result == NO_WORKFLOW:
                draft.set(key, None)
            elif result is not None:
                draft.set(key, result)
            self.error = ""
            return
        if key in {"benchmark", "harness", "model", "skills", "extensions"}:
            options = self.choices(draft, key)
            if not options:
                self.error = f"No configured {key} available"
                return
            if key in {"harness", "skills", "extensions"}:
                names = {item if isinstance(item, str) else item.get("name") for item in
                         (current if isinstance(current, list) else [current] if current else [])}
                result = self.menu(f"Select {key}", [*options, "Done"], names)
                if result is not None:
                    if key == "harness" and not result:
                        self.error = "Select at least one harness"
                        return
                    existing = {item.get("name"): item for item in current or [] if isinstance(item, dict)} if isinstance(current, list) else {}
                    draft.set(key, [existing.get(name, name) for name in result])
            else:
                result = self.menu(f"Select {key}", options)
                if result is not None:
                    draft.set(key, result)
            return
        result = self.prompt(f"Edit {key}", "" if current is None else
                             ", ".join(current) if key == "subset" else str(current))
        if result is None:
            return
        try:
            if key == "subset":
                value = [part for part in re.split(r"[\s,]+", result) if part] or None
            elif key in {"limit", "timeout"}:
                value = int(result) if result else None
            elif key == "max_workers":
                value = int(result)
            else:
                value = result
            draft.set(key, value)
            self.error = ""
        except ValueError:
            self.error = f"{key} must be a whole number"

    def run(self):
        while True:
            start = self.menu("ACB run setup", ["Create new run", "Load YAML file", "Cancel"])
            if start in (None, "Cancel"):
                return None
            try:
                if start == "Load YAML file":
                    path = self.prompt("Path to run YAML")
                    if not path:
                        continue
                    draft = RunDraft.load(path, self.config_dir)
                else:
                    draft = RunDraft.create(self.config_dir)
                break
            except UI_ERRORS as error:
                self.error = str(error)
        labels = {"run_id": "Run ID", "benchmark": "Benchmark", "harness": "Harnesses",
                  "model": "Model", "workflow": "Workflow", "subset": "Task IDs", "limit": "Task limit",
                  "skills": "Skills", "extensions": "Extensions", "timeout": "Timeout (seconds)",
                  "max_workers": "Workers"}
        while True:
            rows = [f"{labels[key]}: {draft.value(key) if draft.value(key) is not None else '—'}" for key in FORM_KEYS]
            action = self.menu("Configure run", [*rows, "Review", "Cancel"])
            if action in (None, "Cancel"):
                return None
            if action != "Review":
                self.edit(draft, FORM_KEYS[rows.index(action)])
                continue
            while True:
                try:
                    plan = resolve(draft.candidate())
                    summary = plan.to_dict()
                    self.error = ""
                    title = f"Review: {summary['run_id']} · {summary['benchmark']} · {summary['model_alias']}"
                    choices = ["Save and run", "Run without saving", "Back", "Cancel"]
                except UI_ERRORS as error:
                    custom = draft.value("workflow")
                    needs_save_origin = (isinstance(error, FileNotFoundError) and draft.source is None
                                         and isinstance(custom, str) and not Path(custom).is_absolute()
                                         and custom not in bundled_workflows())
                    if needs_save_origin:
                        self.error = "Workflow path will be checked relative to the save file"
                        title = "Review: choose a save path"
                        choices = ["Save and run", "Back", "Cancel"]
                    else:
                        self.error = str(error)
                        title = "Review: fix validation error"
                        choices = ["Back", "Cancel"]
                details = [
                    f"Harnesses: {', '.join(draft.config.harnesses)}",
                    f"Workflow: {draft.value('workflow') or 'none'}",
                    f"Tasks: {', '.join(draft.config.subset) if draft.config.subset else 'all'}  Limit: {draft.config.limit or 'none'}",
                    f"Skills: {draft.config.skills or 'none'}  Extensions: {draft.config.extensions or 'none'}",
                    f"Timeout: {draft.value('timeout') or 'default'}  Workers: {draft.value('max_workers')}",
                    f"Save source: {draft.source or 'new file'}",
                ]
                action = self.menu(title, choices, details=details)
                if action in (None, "Back"):
                    break
                if action == "Cancel":
                    return None
                try:
                    if action == "Run without saving":
                        return finish(draft, "unsaved")
                    default = str(draft.source or Path(f"run.{draft.config.run_id}.yaml"))
                    path = self.prompt("Save YAML path", default)
                    if path is None:
                        continue
                    return finish(draft, "save", path or default)
                except UI_ERRORS as error:
                    self.error = str(error)


def configure(config_dir: str | None = None) -> ResolvedPlan | None:
    """Return a validated plan after curses has restored the terminal."""
    return curses.wrapper(lambda window: _Screen(window, config_dir).run())
