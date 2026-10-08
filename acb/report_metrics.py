"""Chart data transformations for captured request and tool observations."""
from collections import defaultdict

from acb.costs import ModelCost, estimate_cost
from acb.usage import normalize_benchmark_metric

COLORS = ["#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
          "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac"]


def color(index):
    return COLORS[index % len(COLORS)]


def per_turn_averages(
    usage_rows: list[dict],
) -> tuple[list[int], list[float | None], list[float | None], list[float | None]]:
    """Average input/output tokens and duration at each turn index."""
    by_turn_input: dict[int, list[float]] = defaultdict(list)
    by_turn_output: dict[int, list[float]] = defaultdict(list)
    by_turn_duration: dict[int, list[float]] = defaultdict(list)
    for r in usage_rows:
        t = r["turn_index"]

        if r.get("input_tokens") is not None:
            by_turn_input[t].append(r["input_tokens"])
        if r.get("output_tokens") is not None:
            by_turn_output[t].append(r["output_tokens"])
        if r.get("duration_ms") is not None:
            by_turn_duration[t].append(r["duration_ms"])

    if not usage_rows:
        return [], [], [], []
    max_turn = max(r["turn_index"] for r in usage_rows)
    turns = list(range(max_turn + 1))
    avg_input = [
        sum(by_turn_input[t]) / len(by_turn_input[t]) if by_turn_input.get(t) else None
        for t in turns
    ]
    avg_output = [
        sum(by_turn_output[t]) / len(by_turn_output[t]) if by_turn_output.get(t) else None
        for t in turns
    ]
    avg_duration: list[float | None] = [
        (sum(by_turn_duration[t]) / len(by_turn_duration[t]))
        if by_turn_duration.get(t)
        else None
        for t in turns
    ]
    return turns, avg_input, avg_output, avg_duration


def aggregate_benchmark_metrics(records: list[dict]) -> dict:
    """Aggregate per-request benchmark_metrics into instance-level metrics.

    Computes totals, averages, and context growth from per-request data.
    Returns a dict compatible with InstanceMetrics structure.

    Note: total_tokens excludes reused cache reads; context_tokens includes them.
    """
    if not records:
        return {
            'turns': 0,
            'total_input': 0,
            'total_output': 0,
            'total_cache_read': 0,
            'total_cache_creation': 0,
            'total_tokens': 0,
            'context_tokens': 0,
            'peak_context': 0,
            'per_turn_prompt': [],
            'cache_efficiency': 0.0,
            'requests': [],
        }

    # Sort by timestamp to ensure turn order
    sorted_records = sorted(
        (normalize_benchmark_metric(r) for r in records),
        key=lambda r: r.get('timestamp_ms', 0),
    )

    for index, record in enumerate(sorted_records):
        record['turn_index'] = index

    # Compute aggregates
    total_input = sum(r.get('input_tokens', 0) for r in sorted_records)
    total_output = sum(r.get('output_tokens', 0) for r in sorted_records)
    total_cache_read = sum(r.get('cache_read_input_tokens', 0) for r in sorted_records)
    total_cache_creation = sum(r.get('cache_creation_input_tokens', 0) for r in sorted_records)

    # Usage-style total excludes reused cache reads. Context volume remains
    # available separately through total_prompt/context metrics.
    total_tokens = total_input + total_output + total_cache_creation

    # Per-turn prompt size (input + cache_read + cache_creation)
    per_turn_prompt = [
        r.get('input_tokens', 0) + r.get('cache_read_input_tokens', 0) + r.get('cache_creation_input_tokens', 0)
        for r in sorted_records
    ]
    peak_context = max(per_turn_prompt) if per_turn_prompt else 0

    # Cache efficiency: cache_read / total_prompt_tokens
    total_prompt = sum(per_turn_prompt)
    context_tokens = total_prompt
    cache_efficiency = total_cache_read / total_prompt if total_prompt > 0 else 0.0

    return {
        'turns': len(sorted_records),
        'total_input': total_input,
        'total_output': total_output,
        'total_cache_read': total_cache_read,
        'total_cache_creation': total_cache_creation,
        'total_tokens': total_tokens,
        'context_tokens': context_tokens,
        'peak_context': peak_context,
        'per_turn_prompt': per_turn_prompt,
        'cache_efficiency': cache_efficiency,
        'requests': sorted_records,  # Keep per-request data for content analysis
    }


def metric_total_tokens(metric: dict) -> float:
    """Return fresh input/creation and output tokens for a classified request."""
    metric = normalize_benchmark_metric(metric)
    return sum(
        metric.get(field, 0) or 0
        for field in (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
        )
    )


def tool_identities(metric: dict) -> list[dict]:
    """Return the current array of normalized tool identities."""
    return metric.get("tools") or []


def normalize_tool_interactions(metrics: list[dict]) -> list[dict]:
    """Combine tool-call and tool-result records into logical interactions.

    The raw metrics remain request-level records. This derived stream is used
    only for tool reporting, so a matching call/result pair is counted once
    while retaining the summed token cost of both model requests.
    """
    interactions: dict[str, dict] = {}
    anonymous_index = 0
    for metric in metrics:
        kind = metric.get("content_type")
        if kind not in {"tool_call", "tool_result"}:
            continue
        tools = tool_identities(metric)
        results = metric.get("tool_results") or []
        events = []
        if kind == "tool_call":
            events.extend(("tool_call", tool) for tool in tools)
            events.extend(("tool_result", tool) for tool in results)
        else:
            events.extend(("tool_result", tool) for tool in (results or tools))
        if not events:
            events = [(kind, {"name": "unknown", "detail": None, "call_id": None})]
        token_share = metric_total_tokens(metric) / len(events)
        for tool_index, (event_kind, tool) in enumerate(events):
            call_id = tool.get("call_id")
            if call_id:
                key = str((metric.get("instance_id"), metric.get("step_name"), call_id))
            else:
                key = f"record:{metric.get('request_id', anonymous_index)}:{tool_index}"
                anonymous_index += 1
            interaction = interactions.setdefault(key, {
                "name": tool.get("name") or "unknown",
                "detail": tool.get("detail"),
                "call_id": call_id,
                "scope": (metric.get("instance_id"), metric.get("step_name")),
                "request_ids": [],
                "source_types": set(),
                "tokens": 0.0,
                "duration_ms": 0.0,
                "turns": [],
            })
            # Prefer the most informative detail if the paired records differ.
            if not interaction["detail"] and tool.get("detail"):
                interaction["detail"] = tool["detail"]
            request_id = metric.get("request_id")
            if request_id and request_id not in interaction["request_ids"]:
                interaction["request_ids"].append(request_id)
            interaction["source_types"].add(event_kind)
            interaction["tokens"] += token_share
            interaction["duration_ms"] += (metric.get("duration_ms") or 0) / len(events)
            turn = metric.get("turn_index")
            if type(turn) is int and turn not in interaction["turns"]:
                interaction["turns"].append(turn)

    normalized = []
    for interaction in interactions.values():
        source_types = interaction.pop("source_types")
        interaction["source_types"] = sorted(source_types)
        interaction["complete_pair"] = source_types == {"tool_call", "tool_result"}
        interaction["call_only"] = source_types == {"tool_call"}
        interaction["result_only"] = source_types == {"tool_result"}
        normalized.append(interaction)
    return normalized


def content_type_breakdown(metrics: list[dict]) -> dict:
    """Generate data for content type visualization."""
    by_type = {}
    tool_usage = {}

    for m in metrics:
        content_type = m.get('content_type', 'unknown')
        if content_type in ['tool_call', 'tool_result']:
            continue
        tokens = metric_total_tokens(m)

        # Aggregate by content type
        if content_type not in by_type:
            by_type[content_type] = {
                'count': 0,
                'total_tokens': 0,
                'turns': []
            }
        by_type[content_type]['count'] += 1
        by_type[content_type]['total_tokens'] += tokens
        by_type[content_type]['turns'].append(m.get('turn_index', 0))

    interactions = normalize_tool_interactions(metrics)
    if interactions:
        by_type['tool_call'] = {
            'count': len(interactions),
            'total_tokens': sum(i['tokens'] for i in interactions),
            'turns': [],
        }
        for interaction in interactions:
            tool_name = interaction['name']
            tool_detail = interaction['detail']
            tool_key = f"{tool_name}:{tool_detail}" if tool_detail else tool_name
            data = tool_usage.setdefault(tool_key, {
                'tool_name': tool_name,
                'tool_detail': tool_detail,
                'interactions': 0,
                'complete_pairs': 0,
                'call_only': 0,
                'result_only': 0,
                'total_tokens': 0.0,
            })
            data['interactions'] += 1
            data['complete_pairs'] += int(interaction['complete_pair'])
            data['call_only'] += int(interaction['call_only'])
            data['result_only'] += int(interaction['result_only'])
            data['total_tokens'] += interaction['tokens']

    return {
        'by_type': by_type,
        'tool_usage': tool_usage
    }


def prepare_timeline_data(metrics: list[dict]) -> dict:
    """Prepare stacked bar chart data showing content types per turn."""
    # Get all turns in order
    turn_set = set()
    for m in metrics:
        turn_idx = m.get('turn_index', 0)
        turn_set.add(turn_idx)

    turns = sorted(turn_set)
    if not turns:
        return {'labels': [], 'datasets': []}

    # Group by content type
    by_type = {}
    for m in metrics:
        content_type = m.get('content_type', 'unknown')
        if content_type not in by_type:
            by_type[content_type] = [0] * len(turns)

        turn_idx = m.get('turn_index', 0)
        if turn_idx in turns:
            data_idx = turns.index(turn_idx)
            by_type[content_type][data_idx] += m.get('input_tokens', 0)

    # Build Chart.js dataset
    colors = {
        'system_prompt': '#4e79a7',
        'user_message': '#59a14f',
        'tool_call': '#f28e2b',
        'tool_result': '#e15759',
        'assistant_continuation': '#76b7b2',
        'mixed': '#edc948',
        'unknown': '#999999'
    }

    datasets = []
    for content_type, data in sorted(by_type.items()):
        datasets.append({
            'label': content_type.replace('_', ' ').title(),
            'data': data,
            'backgroundColor': colors.get(content_type, '#cccccc')
        })

    return {
        'labels': [f"Request {t}" for t in turns],
        'datasets': datasets
    }


def shell_command_breakdown(metrics: list[dict]) -> dict:
    """Aggregate shell commands from normalized call/result interactions.

    Returns:
        {
            'rgctl': {'calls': 15, 'results': 15, 'total_tokens': 40000, 'turns': [...]},
            'git': {'calls': 20, 'results': 20, 'total_tokens': 35000, 'turns': [...]},
            ...
        }
        Sorted by total_tokens (descending)
    """
    commands = {}
    for interaction in normalize_tool_interactions(metrics):
        name = interaction['name']
        detail = interaction['detail']
        if name.lower() in {'bash', 'shell'}:
            command = detail or 'unknown'
        else:
            continue
        entry = commands.setdefault(command, {
            'calls': 0, 'results': 0, 'total_tokens': 0, 'turns': [],
        })
        entry['calls'] += int('tool_call' in interaction['source_types'])
        entry['results'] += int('tool_result' in interaction['source_types'])
        entry['total_tokens'] += interaction['tokens']
        entry['turns'].extend(interaction['turns'])

    # Sort by total tokens (descending)
    return dict(sorted(commands.items(), key=lambda x: x[1]['total_tokens'], reverse=True))


def context_growth_datasets(metrics: list[dict], color_offset: int = 0) -> list[dict]:
    datasets = []
    for i, m in enumerate(metrics):
        per_turn = m.get("per_turn_prompt") or []
        datasets.append({
            "label": m["instance_id"],
            "data": per_turn,
            "borderColor": color(color_offset + i),
            "backgroundColor": color(color_offset + i),
            "fill": False,
            "tension": 0.15,
        })
    return datasets


def cost_datasets(
    usage_rows: list[dict], cost: ModelCost, color_offset: int = 0, label_prefix: str = ""
) -> list[dict]:
    """Cumulative (running-total) USD cost per turn, one line per instance."""
    by_instance: dict[str, list[dict]] = defaultdict(list)
    for r in usage_rows:
        by_instance[r["instance_id"]].append(r)

    datasets = []
    for i, (instance_id, rows) in enumerate(sorted(by_instance.items())):
        rows = sorted(rows, key=lambda r: r["turn_index"])
        cumulative = []
        total = 0.0
        for r in rows:
            total += estimate_cost(
                cost,
                input_tokens=r.get("input_tokens", 0),
                output_tokens=r.get("output_tokens", 0),
                cache_read_tokens=r.get("cache_read_tokens", 0),
                cache_creation_tokens=r.get("cache_creation_tokens", 0),
            )
            cumulative.append(round(total, 6))
        label = f"{label_prefix}{instance_id}" if label_prefix else instance_id
        datasets.append({
            "label": label,
            "data": cumulative,
            "borderColor": color(color_offset + i),
            "backgroundColor": color(color_offset + i),
            "fill": False,
            "tension": 0.15,
        })
    return datasets
