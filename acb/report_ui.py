"""Navigation and small comparison visuals using native HTML and local scripts."""
from acb.html_components import escape, value


def navigation(links, *, label='Report navigation'):
    return ('<nav class="report-nav" aria-label="' + escape(label) + '">' +
            ''.join('<a href="' + escape(href) + '">' + escape(text) + '</a>'
                    for text, href in links if href) + '</nav>')


def sort_header(label, key, *, numeric=False):
    return ('<th><button type="button" data-sort="' + escape(key) +
            '" data-numeric="' + str(numeric).lower() + '" title="Sort by ' + escape(label) + '">' +
            escape(label) + ' <span aria-hidden="true">↕</span></button></th>')


def row_attributes(record, *, outcome, quality='', incomplete=False, **sort_values):
    attributes = {'result-row': '', 'search': record['benchmark'] + ' ' + record['dataset'],
                  'harness': sort_values.pop('harness', record['harness']),
                  'outcome': outcome, 'quality': quality, 'incomplete': str(incomplete).lower(),
                  'benchmark': record['benchmark'], **sort_values}
    return ' '.join('data-' + key + '="' + escape('' if item is None else item) + '"'
                    for key, item in attributes.items())


def table_controls(harnesses, count, *, comparison=False):
    options = lambda items: ''.join('<option value="' + escape(key) + '">' + escape(label) + '</option>'
                                    for key, label in items)
    body = ('<div class="table-controls"><label>Search benchmarks<input type="search" '
            'data-filter="search" placeholder="Name or dataset"></label>')
    if comparison:
        body += ('<label>Grade change<select data-filter="quality"><option value="">All changes</option>' +
                 options([('worse', 'Regressions'), ('better', 'Improvements'), ('same', 'Unchanged'),
                          ('unknown', 'Unknown'), ('not_comparable', 'Not comparable')]) + '</select></label>')
    body += ('<label>' + ('Candidate result' if comparison else 'Result') +
             '<select data-filter="outcome"><option value="">All results</option>' +
             options([(label, label.capitalize()) for label in ('full pass', 'partial pass', 'failure', 'unknown')]) +
             '</select></label><label>Harness<select data-filter="harness"><option value="">All harnesses</option>' +
             options([(name, name) for name in sorted(set(harnesses))]) + '</select></label>' +
             '<label class="checkbox-label"><input type="checkbox" data-filter="incomplete">Incomplete measurements</label>' +
             '<button type="button" data-reset-filters>Reset</button>' +
             f'<span class="result-count" data-result-count aria-live="polite">Showing {count} of {count}</span></div>')
    return body


def paired_tokens(baseline, candidate):
    if baseline is None or candidate is None:
        return '<p class="muted">Comparable token totals unavailable.</p>'
    scale = max(baseline, candidate, 1)
    rows = []
    for label, amount, css in [('Baseline', baseline, 'baseline'), ('Candidate', candidate, 'candidate')]:
        rows.append('<div class="token-bar"><span>' + label + '</span><div class="bar-track" aria-hidden="true">' +
                    f'<div class="bar-fill bar-{css}" style="width:{100 * amount / scale:.3f}%"></div></div>' +
                    '<strong>' + value(amount) + '</strong></div>')
    return '<div class="paired-tokens">' + ''.join(rows) + '</div>'


def quality_distribution(rows):
    states = [('better', 'Improved'), ('same', 'Unchanged'), ('worse', 'Regressed'),
              ('unknown', 'Unknown'), ('not_comparable', 'Not comparable')]
    counts = {key: sum(row['quality'] == key for row in rows) for key, _ in states}
    total = len(rows)
    bars = ''.join(f'<span class="segment segment-{key}" style="width:{100 * counts[key] / total:.3f}%"></span>'
                   for key, _ in states if total and counts[key])
    legend = ''.join('<span><i class="segment-' + key + '" aria-hidden="true"></i>' +
                     escape(label) + ' <strong>' + str(counts[key]) + '</strong></span>' for key, label in states)
    return ('<div class="quality-distribution"><div class="distribution-track" aria-hidden="true">' +
            bars + '</div><div class="distribution-legend">' + legend + '</div></div>')


def run_differences(baseline, candidate):
    changes = []
    for key, label in [('model', 'Model'), ('harnesses', 'Harness'), ('workflow', 'Workflow'),
                       ('skills', 'Skills'), ('extensions', 'Extensions'), ('mcp_servers', 'MCP servers'),
                       ('integrations', 'Integrations'), ('source', 'Source'), ('target', 'Target')]:
        left, right = baseline.get(key), candidate.get(key)
        if left == right:
            continue
        def display(item, info):
            if isinstance(item, list):
                return ', '.join(item) if item else ('None' if info.get('configuration_recorded') else 'Not recorded')
            return str(item) if item is not None else ('None' if info.get('configuration_recorded') else 'Not recorded')
        changes.append('<tr><th>' + label + '</th><td>' + escape(display(left, baseline)) +
                       '</td><td>' + escape(display(right, candidate)) + '</td></tr>')
    if not changes:
        return '<p class="muted">Displayed model, workflow, skills, and harness settings match.</p>'
    return ('<details class="run-differences" open><summary>Run differences</summary><table>' +
            '<thead><tr><th>Setting</th><th>Baseline</th><th>Candidate</th></tr></thead><tbody>' +
            ''.join(changes) + '</tbody></table></details>')
