"""HTML presentation for the shared per-benchmark comparison contract."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import html
import json
from pathlib import Path

from acb.comparison import benchmarks, compare
from acb.utils import normalize_instance_id_for_path


def esc(value):
    return html.escape(str(value))


def value(item):
    return 'unavailable' if item is None else esc(round(item, 3) if isinstance(item, float) else item)


def percent(item):
    return 'unavailable' if item is None else value(item) + '%'


def page(title, body):
    return f'''<!doctype html><html><head><meta charset="utf-8"><title>{esc(title)}</title>
<style>body{{font:16px system-ui;max-width:1300px;margin:32px auto;padding:0 20px;color:#17202a}}table{{border-collapse:collapse;width:100%;margin:20px 0}}td,th{{padding:10px;text-align:left;border-bottom:1px solid #ddd;vertical-align:top}}td:first-child small{{overflow-wrap:anywhere;max-width:280px}}th:nth-child(4),td:nth-child(4){{min-width:80px}}small{{display:block;color:#555}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;max-height:600px;overflow:auto}}.warning{{background:#fff3cd;padding:12px}}a{{color:#185abc}}details{{margin:12px 0}}</style></head><body>{body}</body></html>'''


def filename(identity):
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24] + '.html'


def render_comparison(payload, links=None):
    links = links or {}
    coverage = payload['coverage']
    verdict = payload['quality'] or 'No comparable grades'
    qualifier = '' if coverage['complete'] else ' — incomplete comparison'
    body = f'<h1>Benchmark comparison</h1><h2>{esc(verdict.capitalize())}{qualifier}</h2>'
    body += f'<p>Baseline: {esc(payload["baseline"])}<br>Candidate: {esc(payload["candidate"])}</p>'
    body += f'<p>Candidate relative to baseline. Graded coverage: {coverage["graded"]}/{coverage["total"]}; comparable token measurements: {coverage["measured"]}/{coverage["total"]}.</p>'
    body += '<p>Quality is compared per benchmark. No universal suite quality score is calculated. Tokens include fresh input, output, cache reads and cache creation.</p>'
    body += '<table><tr><th>Benchmark / harness</th><th>Baseline grade</th><th>Candidate grade</th><th>Quality</th><th>Tokens: baseline → candidate</th><th>Token change</th><th>Comparability / detail</th></tr>'
    for row in payload['rows']:
        left, right = row['baseline'], row['candidate']
        label = (left or right)['benchmark']
        def detail_link(side):
            record = row[side]
            return links.get((side, (record['dataset'], record['benchmark'], record['harness']))) if record else None
        notices = list(row['reasons']) + row['telemetry_notes']
        if left and right and left['model'] != right['model']:
            notices.append('Models differ; token counts may use different tokenizers and are not a price comparison')
        if not row['measurement_comparable']:
            notices.append('Token comparison unavailable: unmatched conditions or incomplete measurements')
        if row['quality'] == 'unknown':
            notices.append('Grade missing or evaluation failed')
        link_text = ' '.join(f'<a href="{esc(detail_link(side))}">{side} detail</a>' for side in ('baseline', 'candidate') if detail_link(side))
        body += f'<tr><td><strong>{esc(label)}</strong><small>{esc((left or right)['dataset'])}</small><small>{esc(left['harness'] if left else 'absent')} → {esc(right['harness'] if right else 'absent')}</small></td><td>{value(left["grade"] if left else None)}</td><td>{value(right["grade"] if right else None)}</td><td>{esc(row["quality"])}</td>'
        body += f'<td>{value(left["tokens"] if left else None)} → {value(right["tokens"] if right else None)}</td><td>{value(row["tokens"]["absolute"])}<small>{percent(row["tokens"]["percent"])}</small></td><td>{"<br>".join(esc(n) for n in notices)}<br>{link_text}</td></tr>'
        body += '<tr><td colspan="7"><details><summary>Turns, requests, tools and token buckets</summary><table><tr><th>Measure</th><th>Baseline</th><th>Candidate</th><th>Change</th></tr>'
        from acb.comparison import delta
        for field in ('turns', 'model_requests', 'tool_calls'):
            a,b = (left.get(field) if left else None),(right.get(field) if right else None)
            body += f'<tr><td>{esc(field)}</td><td>{value(a)}</td><td>{value(b)}</td><td>{value(delta(a,b)["absolute"]) if row["diagnostic_comparability"][field] else "not comparable"}</td></tr>'
        for side, record in [('Baseline', left), ('Candidate', right)]:
            if record and not record['measurement_complete']:
                body += '<tr><td colspan="4">'+side+' captured observations (incomplete): '+', '.join(
                    field.replace('_',' ')+': '+value(record['captured_'+field])
                    for field in ('turns','model_requests','tool_calls'))+'</td></tr>'
        body += '</table><p>Primary grade: '+esc((left or right)['definition'])+'.</p><p>Turns, model requests and tool calls are distinct. Unavailable telemetry is not zero; different trajectories are not aligned call-by-call.</p>'
        for side, record in [('Baseline',left),('Candidate',right)]:
            if record:
                body += f'<h3>{side}</h3><pre>{esc(json.dumps({"buckets":record["token_buckets"],"captured_tokens":record["captured_tokens"],"provenance":record["provenance"]},indent=2))}</pre>'
        body += '</details></td></tr>'
    body += '</table>'
    total = payload['matched_tokens']
    if coverage['measured']:
        body += f'<h2>Tokens over the {coverage["measured"]} comparable benchmarks only</h2><p>{value(total["baseline"])} → {value(total["candidate"])}; change {value(total["absolute"])} ({percent(total["percent"])}).</p>'
    return page('Benchmark comparison', body)


def detail_html(record):
    # Keep the existing detailed charts, tool breakdowns and patch views, filtered
    # to this benchmark's trial identities rather than showing the whole suite.
    from acb.html_report import _RunData, _build_single_run_html
    directory = Path(record['directory'])
    rd = _RunData.load(directory)
    ids = {t['id'] for t in record['trials']}
    selected = replace(rd, metrics=[m for m in rd.metrics if m['instance_id'] in ids],
                       usage_rows=[u for u in rd.usage_rows if u['instance_id'] in ids],
                       predictions={k:v for k,v in rd.predictions.items() if k in ids})
    selected.report = {**rd.report, 'instances': len(ids), 'run_id': record['benchmark']}
    # Whole-run averages are not benchmark values: omit misleading summary cards.
    rendered = _build_single_run_html(selected, include_summary=False)
    rendered = rendered.replace('<h1>'+esc(record['benchmark'])+'</h1>', '<h2>Request charts and tool breakdowns</h2>', 1)
    evidence = f'<style>.benchmark-evidence pre{{white-space:pre-wrap;overflow-wrap:anywhere;max-height:600px;overflow:auto}}.benchmark-evidence details{{margin:12px 0}}.benchmark-evidence td{{overflow-wrap:anywhere}}</style><section class="benchmark-evidence"><h1>{esc(record["benchmark"])}</h1><p>Grade: {value(record["grade"])}; definition: {esc(record["definition"])}. Tokens: {value(record["tokens"])}; captured tokens: {record["captured_tokens"]}.</p>'
    outcomes = [trial['evaluation'].get('status', 'unknown') + (
        ' ('+str(trial['evaluation']['exception'].get('exception_type', 'evaluation error'))+')'
        if trial['evaluation'].get('exception') else '') for trial in record['trials']]
    evidence += '<p>Trial outcomes: '+esc(', '.join(outcomes))+'.</p>'
    evidence += '<details><summary>Configuration and comparability provenance</summary><pre>'+esc(json.dumps({key: record[key] for key in ('dataset','harness','model','provenance','grades','grade_aggregation','measurement_complete','token_buckets')},indent=2))+'</pre></details>'
    evidence += '<p>Agent turns: '+value(record['turns'])+'; model requests: '+value(record['model_requests'])+'; tool calls: '+value(record['tool_calls'])+'.</p>'
    if not record['measurement_complete']:
        evidence += '<p>Captured observations (incomplete): turns '+value(record['captured_turns'])+', model requests '+value(record['captured_model_requests'])+', tool calls '+value(record['captured_tool_calls'])+'. These are partial observations, not comparable totals.</p>'
    if record.get('dataset_metrics'):
        evidence += '<details><summary>Dataset-native metrics (whole configuration, not this benchmark grade)</summary><pre>'+esc(json.dumps(record['dataset_metrics'],indent=2))+'</pre></details>'
    for trial in record['trials']:
        evidence += f'<details><summary>Trial {esc(trial["id"])} — grade, requests and artifacts</summary><pre>{esc(json.dumps(trial,indent=2))}</pre>'
        trial_dir = directory/'instances'/normalize_instance_id_for_path(trial['id'])
        if trial_dir.is_dir():
            for artifact in sorted(trial_dir.rglob('*')):
                if artifact.is_file() and artifact.suffix in ('.json','.jsonl','.txt','.log','.patch'):
                    evidence += f'<details><summary>{esc(artifact.relative_to(trial_dir))}</summary><pre>{esc(artifact.read_text(errors="replace"))}</pre></details>'
        evidence += '</details>'
    evidence += '<h2>Recorded model requests</h2><p>Indices refer to model requests, independently of agent turns. Tool arguments and results are in the trial trajectory above.</p><table><tr><th>Trial / step</th><th>Request index</th><th>Fresh input</th><th>Output</th><th>Cache read</th><th>Cache creation</th><th>Duration (ms)</th><th>HTTP status</th></tr>'
    for trial in record['trials']:
        for request in trial['requests']:
            evidence += '<tr><td>'+esc(trial['id'])+' / '+esc(request.get('step_name') or 'single')+'</td>'
            for key in ('turn_index','input_tokens','output_tokens','cache_read_tokens','cache_creation_tokens','duration_ms','status_code'):
                item = request.get(key)
                if key == 'status_code' and (not isinstance(item, int) or not 100 <= item <= 599):
                    item = None
                evidence += '<td>'+value(item)+'</td>'
            evidence += '</tr>'
    evidence += '</table></section>'
    return rendered.replace('<body>', '<body>'+evidence, 1)


def write_reports(run_dirs, destination):
    roots = [Path(run_dirs)] if isinstance(run_dirs,(str,Path)) else list(map(Path,run_dirs))
    for root in roots:
        if not root.is_dir():
            raise FileNotFoundError(f"run directory does not exist: {root}")
    destination = Path(destination)
    destination.parent.mkdir(parents=True,exist_ok=True)
    assets = destination.parent/(destination.stem+'-benchmarks')
    assets.mkdir(exist_ok=True)
    links = {}
    index = []
    for i, root in enumerate(roots):
        side = 'run' if len(roots)==1 else ('baseline' if i==0 else ('candidate' if i==1 else f'candidate-{i}'))
        for identity, record in benchmarks(root).items():
            name = side+'-'+filename(identity)
            (assets/name).write_text(detail_html(record))
            link = assets.name+'/'+name
            links[(side,identity)] = link
            index.append(f'<li><a href="{esc(link)}">{esc(side+": "+" / ".join(identity))}</a> — grade {value(record["grade"])}</li>')
    if len(roots)>1:
        payloads = [compare(roots[0],root) for root in roots[1:]]
        documents=[]
        for i,payload in enumerate(payloads,1):
            candidate_side = 'candidate' if i==1 else f'candidate-{i}'
            local_links = {('candidate' if side==candidate_side else side,key):link for (side,key),link in links.items() if side in ('baseline',candidate_side)}
            documents.append(render_comparison(payload,local_links).split('<body>',1)[1].rsplit('</body>',1)[0])
        destination.write_text(page('Benchmark comparisons',''.join(documents)))
        destination.with_suffix('.comparison.json').write_text(json.dumps(payloads,indent=2))
    else:
        destination.write_text(page('Benchmark reports','<h1>Individual benchmark reports</h1><ul>'+''.join(index)+'</ul>'))
    return destination
