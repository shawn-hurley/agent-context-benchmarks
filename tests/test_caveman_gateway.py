import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location('caveman_gateway', Path(__file__).resolve().parents[1] / 'acb/integrations/assets/caveman/server.py')
gateway = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gateway)


@pytest.fixture(autouse=True)
def evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway, 'EVIDENCE', tmp_path / 'evidence.jsonl')
    monkeypatch.setenv('ACB_CAVEMAN_MODE', 'compress')


def test_noneligible_payloads_keep_original_bytes():
    body = b'{ "model": "qwen", "stream": true, "messages": [{"role":"tool", "tool_call_id":"id1", "content":"source code"}], "temperature": 0 }'
    assert gateway.transform(body) == body
    assert gateway.transform(b'not json') == b'not json'


def test_errors_and_source_are_never_compressed():
    assert not gateway.eligible('INFO normal log\n' * 500 + 'ERROR tests failed\n')
    assert not gateway.eligible('def function(): return 42\n' * 500)
    assert not gateway.eligible('Traceback (most recent call last)\n' * 500)
    data = {'messages': [{'role': 'user', 'content': [{'type': 'tool_result', 'is_error': True, 'tool_use_id': 'abc', 'content': 'INFO lines\n' * 500}]}]}
    body = json.dumps(data).encode()
    assert gateway.transform(body) == body


def test_record_mode_does_not_call_engine(monkeypatch):
    monkeypatch.setenv('ACB_CAVEMAN_MODE', 'record')
    original = 'INFO repeated event\n' * 500
    def forbidden(*args, **kwargs):
        raise AssertionError('engine called')
    assert gateway.compress(original, forbidden) == original


def test_compression_requires_exact_recovery():
    original = 'INFO repeated event\n' * 500
    def runner(command, **kwargs):
        if command[1] == 'compress':
            return SimpleNamespace(stdout=b'INFO repeated event x500', stderr=b'{"recovery_handle":"abc123","tokens_before":1000,"tokens_after":5}')
        return SimpleNamespace(stdout=original.encode())
    result = gateway.compress(original, runner)
    assert 'acb-recall abc123' in result
    assert len(result) < len(original)
    events = [json.loads(line) for line in gateway.EVIDENCE.read_text().splitlines()]
    assert events[-1]['status'] == 'compressed'


def test_failed_recovery_falls_back_to_exact_original():
    original = 'INFO repeated event\n' * 500
    def runner(command, **kwargs):
        if command[1] == 'compress':
            return SimpleNamespace(stdout=b'INFO shorter', stderr=b'{"recovery_handle":"abc123"}')
        return SimpleNamespace(stdout=b'wrong original')
    assert gateway.compress(original, runner) == original
    event = json.loads(gateway.EVIDENCE.read_text())
    assert event['status'] == 'passthrough'
    assert event['recovery_verified'] is False


def test_only_stdout_changes_and_cache_tool_metadata_survive(monkeypatch):
    monkeypatch.setattr(gateway, 'compress', lambda text: 'compressed')
    data = {'model': 'qwen', 'stream': True, 'tools': [{'function': {'name': 'bash'}}],
            'messages': [{'role': 'tool', 'tool_call_id': 'tool-1', 'cache_control': {'type': 'ephemeral'},
                          'content': json.dumps({'exit_code': 0, 'stdout': 'INFO log', 'stderr': ''})}]}
    actual = json.loads(gateway.transform(json.dumps(data).encode()))
    assert actual['model'] == data['model']
    assert actual['tools'] == data['tools']
    assert actual['stream'] is True
    message = actual['messages'][0]
    assert message['tool_call_id'] == 'tool-1'
    assert message['cache_control'] == {'type': 'ephemeral'}
    assert json.loads(message['content']) == {'exit_code': 0, 'stdout': 'compressed', 'stderr': ''}


def test_nested_anthropic_text_preserves_tool_ids_cache_and_images(monkeypatch):
    monkeypatch.setattr(gateway, 'compress', lambda text: 'compressed')
    data = {'messages': [{'role': 'user', 'content': [
        {'type': 'tool_result', 'is_error': False, 'tool_use_id': 'one',
         'cache_control': {'type': 'ephemeral'}, 'content': [
             {'type': 'text', 'text': 'logs', 'cache_control': {'type': 'ephemeral'}},
             {'type': 'image', 'source': {'type': 'base64', 'data': 'opaque'}}]},
        {'type': 'tool_result', 'is_error': True, 'tool_use_id': 'two', 'content': 'failed logs'},
        {'type': 'tool_result', 'tool_use_id': 'unknown', 'content': 'unknown status'},
    ]}]}
    result = json.loads(gateway.transform(json.dumps(data).encode()))
    expected = json.loads(json.dumps(data))
    expected['messages'][0]['content'][0]['content'][0]['text'] = 'compressed'
    assert result == expected


def test_pi_adapter_preserves_failures_nontext_and_recovery_outputs(tmp_path):
    import shutil
    import subprocess
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for native Pi adapter contract')
    source = (Path(__file__).resolve().parents[1] / 'acb/integrations/assets/caveman/pi.ts').read_text()
    source = source.replace('import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";', '')
    source = source.replace('export default function (pi: ExtensionAPI)', 'function install(pi)')
    script = source + """
import assert from 'node:assert/strict';
let callback;
install({on: (name, fn) => { assert.equal(name, 'tool_result'); callback = fn; }});
const text = 'INFO repeated event\\n'.repeat(500);
const event = {toolName:'bash', isError:false, input:{command:'cat log'},
               content:[{type:'text',text}]};
const result = await callback(event);
assert.deepEqual(JSON.parse(result.content[0].text),
                 {acb_caveman:1,exit_code:0,stdout:text});
for (const patch of [
    {isError:true}, {isError:undefined}, {toolName:'read'},
    {input:{command:'acb-recall abc'}},
    {content:[{type:'image',data:'opaque'}]},
    {content:[{type:'text',text:text+'ERROR critical detail\\n'}]},
]) assert.equal(await callback({...event,...patch}), undefined);
assert.equal(event.content[0].text,text);
"""
    subprocess.run([node, '--input-type=module', '-e', script], check=True, capture_output=True)


def test_failed_retrieval_does_not_count_as_agent_verification(tmp_path):
    from acb.integrations.caveman import CavemanIntegration
    from acb.integrations.base import IntegrationContext
    root = tmp_path / 'runtime'
    root.mkdir()
    (root / 'evidence.jsonl').write_text(json.dumps({'status': 'retrieved', 'success': False}) + '\n')
    transport = SimpleNamespace(service_download=lambda *args, **kwargs: None, service_capture=lambda *args: "ready")
    integration = CavemanIntegration({})
    integration.metadata = {}
    integration.collect(IntegrationContext(transport, 'arm64', 'pi', 'test', tmp_path, tmp_path))
    assert not integration.metadata['activity']['agent_tool_verified']
    assert not integration.metadata['activity']['compression_verified']


@pytest.mark.parametrize('command,status,compressed', [
    ("printf 'INFO repeated event\\n%.0s' {1..500}", 0, True),
    ("printf 'INFO repeated event\\n%.0s' {1..500}; exit 7", 7, False),
    ("printf 'INFO repeated event\\n%.0s' {1..500}; printf 'critical' >&2", 0, False),
    ("printf 'INFO repeated event\\n%.0s' {1..500}; printf 'ERROR critical\\n'", 0, False),
    ("printf '\\377\\000'", 0, False),
])
def test_goose_status_bytes_and_stderr(command, status, compressed):
    import subprocess
    import sys
    script = Path(__file__).resolve().parents[1] / 'acb/integrations/assets/caveman/goose.py'
    original = subprocess.run(['/bin/bash', '-c', command], capture_output=True)
    actual = subprocess.run([sys.executable, str(script), '-c', command], capture_output=True)
    assert actual.returncode == original.returncode == status
    assert actual.stderr == original.stderr
    if compressed:
        assert json.loads(actual.stdout)['stdout'].encode() == original.stdout
        assert json.loads(actual.stdout)['exit_code'] == 0
    else:
        assert actual.stdout == original.stdout


def test_opencode_adapter_requires_numeric_success_and_preserves_metadata():
    import shutil
    import subprocess
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for native OpenCode adapter contract')
    source = (Path(__file__).resolve().parents[1] / 'acb/integrations/assets/caveman/opencode.ts').read_text()
    source = source.replace('import type { Plugin } from "@opencode-ai/plugin";', '')
    source = source.replace('const CavemanPlugin: Plugin', 'const CavemanPlugin').replace('new Set<string>()', 'new Set()')
    script = source + """
import assert from 'node:assert/strict';
const hooks = await CavemanPlugin();
const input = {tool:'bash',sessionID:'a',callID:'one'};
const text = 'INFO repeated event\\n'.repeat(500);
for (const exit of [undefined, 7, '0', 0]) {
  const output = {output:text,metadata:{exit,extra:'preserved'},title:'title'};
  await hooks['tool.execute.after'](input,output);
  assert.deepEqual(output.metadata,{exit,extra:'preserved'});
  assert.equal(output.title,'title');
  if (exit === 0) assert.equal(JSON.parse(output.output).stdout,text);
  else assert.equal(output.output,text);
}
await hooks['tool.execute.before'](input,{args:{command:'acb-recall handle'}});
const output = {output:text,metadata:{exit:0}};
await hooks['tool.execute.after'](input,output);
assert.equal(output.output,text);
"""
    subprocess.run([node, '--input-type=module', '-e', script], check=True, capture_output=True)


def test_service_loss_fails_collection_after_exporting_evidence(tmp_path):
    from acb.integrations.caveman import CavemanIntegration
    from acb.integrations.base import IntegrationContext, IntegrationFailure
    exported = []
    def failed(*args):
        raise RuntimeError('service exited')
    transport = SimpleNamespace(service_capture=failed, service_download=lambda *args, **kwargs: exported.append(args))
    integration = CavemanIntegration({})
    integration.metadata = {}
    with pytest.raises(IntegrationFailure, match='required Caveman service'):
        integration.collect(IntegrationContext(transport, 'arm64', 'pi', 'test', tmp_path, tmp_path))
    assert exported


def test_engine_fallback_keeps_bytes_but_invalidates_required_treatment(tmp_path):
    from acb.integrations.caveman import CavemanIntegration
    from acb.integrations.base import IntegrationContext, IntegrationFailure
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    (runtime / 'evidence.jsonl').write_text(json.dumps({
        'status': 'passthrough', 'reason': 'CalledProcessError', 'recovery_verified': False}) + '\n')
    transport = SimpleNamespace(service_capture=lambda *a: 'ready', service_download=lambda *a, **kw: None)
    integration = CavemanIntegration({})
    integration.metadata = {}
    with pytest.raises(IntegrationFailure, match='engine failed'):
        integration.collect(IntegrationContext(transport, 'arm64', 'pi', 'test', tmp_path, tmp_path))
