import json
from pathlib import Path

import pytest

from acb.comparison import compare
from acb.comparison_html import write_reports


def run(root, values, *, revision='a', complete=True, tokens=100):
    root.mkdir(parents=True)
    records=[]; usage=[]
    for key,grade in values.items():
        records.append({'task_id':key,'trial_id':key,'resolved':bool(grade) if grade is not None else None,
                        'rewards':{'score':grade},'status':'completed' if grade is not None else 'error',
                        'measurement_complete':complete})
        usage.append({'run_id':root.name,'benchmark':'dataset','harness':'pi','model':'model','turn_index':0,'instance_id':key,'input_tokens':tokens,'output_tokens':0,'cache_read_tokens':0,'cache_creation_tokens':0})
    (root/'report.json').write_text(json.dumps({'run_id':root.name,'benchmark':'suite','dataset':'dataset',
        'harness':'pi','model':'model','instances':len(records),'evaluations':records,
        'grade_definition':{'metric':'score','direction':'higher','tolerance':0},
        'comparison_provenance':{'conditions':{'timeout':100},'tasks':{k:{'revision':revision} for k in values}}}))
    (root/'usage.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in usage))
    return root


@pytest.mark.parametrize('before,after,expected',[
    ([0,1],[0,1],'same'),([0,1],[1,1],'better'),([1,1],[1,0],'worse'),([0,1],[1,0],'mixed')])
def test_directional_quality(tmp_path,before,after,expected):
    a=run(tmp_path/'a',{str(i):v for i,v in enumerate(before)});b=run(tmp_path/'b',{str(i):v for i,v in enumerate(after)},tokens=50)
    result=compare(a,b)
    assert result['quality']==expected
    assert result['coverage']['complete']
    assert result['matched_tokens']['percent']==-50


def test_partial_and_invalid_results_do_not_become_same(tmp_path):
    a=run(tmp_path/'a',{'one':0,'two':1,'three':1})
    b=run(tmp_path/'b',{'one':1,'two':None,'extra':1},complete=False)
    result=compare(a,b)
    assert result['quality']=='better'
    assert result['coverage']=={'graded':1,'total':4,'complete':False,'measured':0}
    assert all(row['tokens']['absolute'] is None for row in result['rows'])
    candidate=next(row['candidate'] for row in result['rows'] if row['identity'][1]=='one')
    assert candidate['captured_model_requests']==1
    assert candidate['captured_tokens']==100 and candidate['tokens'] is None


def test_changed_revision_and_zero_baseline(tmp_path):
    a=run(tmp_path/'a',{'one':1},tokens=0);b=run(tmp_path/'b',{'one':1},revision='b')
    assert compare(a,b)['quality'] is None
    report=json.loads((b/'report.json').read_text());report['comparison_provenance']['tasks']['one']['revision']='a'
    (b/'report.json').write_text(json.dumps(report))
    result=compare(a,b)
    assert result['matched_tokens']['absolute']==100
    assert result['matched_tokens']['percent'] is None


def test_missing_provenance_is_not_a_match(tmp_path):
    a=run(tmp_path/'a',{'one':1});b=run(tmp_path/'b',{'one':1})
    for root in (a,b):
        r=json.loads((root/'report.json').read_text());r.pop('comparison_provenance');(root/'report.json').write_text(json.dumps(r))
    assert compare(a,b)['quality'] is None


def test_html_writes_individual_pages_and_escapes_evidence(tmp_path):
    roots=[run(tmp_path/'a',{'task<unsafe>':0}),run(tmp_path/'b',{'task<unsafe>':1,'unmatched':0})]
    for root in roots:
        rows=[json.loads(l) for l in (root/'usage.jsonl').read_text().splitlines()]
        for r in rows:
            r.update(run_id=root.name,benchmark='dataset',harness='pi',model='model',turn_index=0)
            artifact=root/r['instance_id'];artifact.mkdir(parents=True)
            (artifact/'transcript.jsonl').write_text(json.dumps({'type':'turn_start'})+'\n'+json.dumps({'type':'tool_execution_start','toolCallId':'call','args':{'command':'<script>alert(1)</script>'}})+'\n')
        (root/'usage.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    destination=tmp_path/'review'/'comparison.html'
    write_reports(roots,destination)
    text=destination.read_text()
    assert 'incomplete comparison' in text
    assert 'task&lt;unsafe&gt;' in text
    pages=list((destination.parent/'comparison-benchmarks').glob('*.html'))
    assert len(pages)==3
    assert all('<script>alert(1)</script>' not in p.read_text() for p in pages)
    assert any('tool_execution_start' in p.read_text() for p in pages)
    result=compare(*roots)
    row=next(r for r in result['rows'] if r['identity'][1]=='task<unsafe>')
    assert row['baseline']['turns']==1
    assert row['baseline']['tool_calls']==1


def test_cross_harness_comparison_matches_benchmark_and_links(tmp_path):
    a=run(tmp_path/'a',{'one':1});b=run(tmp_path/'b',{'one':0})
    r=json.loads((b/'report.json').read_text());r['harness']='goose';(b/'report.json').write_text(json.dumps(r))
    result=compare(a,b)
    assert result['quality']=='worse' and result['coverage']['complete']
    write_reports([a,b],tmp_path/'report.html')
    import re
    links=re.findall('href="([^"]+)"',(tmp_path/'report.html').read_text())
    detail_links = [link for link in links if link.endswith('.html')]
    assert len(detail_links)==2
    assert all((tmp_path/link).exists() for link in detail_links)
    from acb.comparison_html import build_report
    assert 'Worse' in build_report([a,b])


def test_empty_comparable_set_has_no_token_total(tmp_path):
    a=run(tmp_path/'a',{'one':1});b=run(tmp_path/'b',{'two':0})
    assert compare(a,b)['matched_tokens']['baseline'] is None
    assert compare(a,b)['matched_tokens']['absolute'] is None


def test_budget_and_unknown_task_provenance_are_not_comparable(tmp_path):
    a=run(tmp_path/'a',{'one':1});b=run(tmp_path/'b',{'one':1})
    r=json.loads((b/'report.json').read_text());r['comparison_provenance']['conditions']['timeout']=200
    (b/'report.json').write_text(json.dumps(r))
    assert not compare(a,b)['rows'][0]['comparable']
    for root in (a,b):
        r=json.loads((root/'report.json').read_text());r['comparison_provenance']['tasks']['one']={'revision':None}
        (root/'report.json').write_text(json.dumps(r))
    assert compare(a,b)['quality'] is None


def test_lower_is_better_and_tolerance_is_respected(tmp_path):
    a=run(tmp_path/'a',{'one':10});b=run(tmp_path/'b',{'one':8})
    for root in (a,b):
        r=json.loads((root/'report.json').read_text());r['grade_definition'].update(direction='lower',tolerance=.5)
        (root/'report.json').write_text(json.dumps(r))
    assert compare(a,b)['quality']=='better'


def test_tool_counts_deduplicate_stream_fragments_not_parallel_calls(tmp_path):
    from acb.telemetry import trajectory
    events=[{'type':'assistant','message':{'role':'assistant','id':'turn-1','content':[
        {'type':'tool_use','id':'a'},{'type':'tool_use','id':'b'}]}}]*2
    events.append({'type':'assistant','message':{'role':'assistant','id':'turn-2','content':[]}})
    path=tmp_path/'transcript.jsonl';path.write_text('\n'.join(json.dumps(x) for x in events))
    observed=trajectory(path,'claude-code')
    assert observed['turns']==2 and observed['tool_calls']==2
    assert trajectory(tmp_path/'missing','pi')['tool_calls'] is None



def test_missing_usage_file_is_not_zero_usage(tmp_path):
    a=run(tmp_path/'a',{'one':1});b=run(tmp_path/'b',{'one':1})
    (b/'usage.jsonl').unlink()
    result=compare(a,b)
    assert result['quality']=='same'
    assert result['rows'][0]['candidate']['tokens'] is None
    assert result['matched_tokens']['absolute'] is None


def test_missing_input_does_not_overwrite_report(tmp_path):
    destination=tmp_path/'report.html';destination.write_text('preserved')
    with pytest.raises(FileNotFoundError):
        write_reports(tmp_path/'missing',destination)
    assert destination.read_text()=='preserved'
