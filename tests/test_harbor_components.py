"""Configured capabilities must reach the executable, not only a config file."""
import json
import shlex

import pytest

from acb.harnesses import claude_code
from acb.harnesses import goose
from acb.harnesses.pi import Pi
from acb.mcp import MCPServerManager


@pytest.mark.parametrize('profile', ['bare', 'isolated-hooks'])
def test_claude_launch_loads_only_explicit_mcp_servers(monkeypatch, tmp_path, profile):
    adapter = claude_code.ClaudeCode({'launch_profile': profile, 'conda_env': None,
                                    'mcp_servers': [{'name': 'fixture', 'command': '/fixture'}]})
    copied = {}
    monkeypatch.setattr(claude_code, 'container_cp_in',
                        lambda container, source, destination: copied.update({destination: json.loads(source.read_text())}))
    adapter._write_mcp_config('container', adapter.config['mcp_servers'])
    commands = []
    monkeypatch.setattr(claude_code, 'execute', lambda command, **kwargs: commands.append(command))
    adapter.run_container('Fix this', 'container', 'model', {}, tmp_path, 'fixture')
    argv = shlex.split(commands[0][-1])[1:]
    path = argv[argv.index('--mcp-config') + 1]
    assert copied[path]['mcpServers']['fixture']['command'] == '/fixture'
    assert '--strict-mcp-config' in argv
    assert argv[argv.index('--allowedTools') + 1] == 'Bash,Edit,Read,mcp__fixture'


def test_goose_launch_preserves_selected_mcp_and_builtin_tools(monkeypatch, tmp_path):
    import yaml

    adapter = goose.Goose({'conda_env': None, 'mcp_servers': [
        {'name': 'fixture', 'command': '/fixture', 'args': ['--stdio'],
         'env': {'FIXTURE_MODE': 'test'}}]})
    copied = []
    monkeypatch.setattr(goose, 'container_exec_capture', lambda *args: '')
    monkeypatch.setattr(goose, 'container_cp_in',
                        lambda container, source, destination: copied.append(yaml.safe_load(source.read_text())))
    adapter._write_mcp_config('container', adapter.config['mcp_servers'])
    launched = []
    monkeypatch.setattr(goose, 'execute', lambda *args, **kwargs: launched.append(copied[-1]))
    adapter.run_container('Fix this', 'container', 'model', {}, tmp_path, 'fixture')
    assert len(copied) == 2
    extensions = launched[0]['extensions']
    assert extensions['fixture']['cmd'] == '/fixture'
    assert extensions['fixture']['args'] == ['--stdio']
    assert extensions['fixture']['env'] == {'FIXTURE_MODE': 'test'}
    assert extensions['developer']['enabled'] is True
    assert extensions['skills']['enabled'] is True
    assert extensions['fetch']['enabled'] is False


def test_goose_mcp_cannot_replace_builtin(monkeypatch):
    adapter = goose.Goose({'mcp_servers': [{'name': 'developer', 'command': '/fixture'}]})
    with pytest.raises(ValueError, match='conflict with Goose built-ins'):
        adapter._inject_goose_config('container')


@pytest.mark.parametrize('servers', [
    [None], [{'name': 'bad name', 'command': '/fixture'}],
    [{'name': 'fixture'}], [{'name': 'fixture', 'command': '/fixture', 'args': 'bad'}],
    [{'name': 'fixture', 'command': '/fixture', 'env': {'COUNT': 1}}],
    [{'name': 'fixture', 'command': '/fixture', 'transport': 'sse'}],
    [{'name': 'fixture', 'command': '/fixture'}] * 2,
])
@pytest.mark.parametrize('harness', ['goose', 'pi', 'opencode', 'claude-code'])
def test_mcp_invalid_selections_fail_before_config_delivery(servers, harness):
    with pytest.raises(ValueError):
        MCPServerManager().generate_config(servers, harness)


def test_pi_mcp_cannot_silently_run_without_tools():
    adapter = Pi({'mcp_servers': [{'name': 'fixture', 'command': '/fixture'}]})
    with pytest.raises(NotImplementedError, match='does not load mcp_servers.json'):
        adapter.setup_mcp_servers('container', 'arm64', None)


def test_harbor_task_mcp_reaches_adapter_without_mutating_plan():
    pytest.importorskip('harbor')
    from harbor.models.task.config import MCPServerConfig
    from acb.harbor.components import with_task_mcp

    original = {'mcp_servers': [{'name': 'selected', 'command': '/selected'}]}
    task = MCPServerConfig(name='task', transport='stdio', command='/task', args=['/work'])
    effective = with_task_mcp(original, [task], 'claude-code')
    assert [server['name'] for server in effective['mcp_servers']] == ['selected', 'task']
    assert effective['mcp_servers'][1]['args'] == ['/work']
    assert len(original['mcp_servers']) == 1
    collision = MCPServerConfig(name='selected', transport='stdio', command='/task')
    with pytest.raises(ValueError, match='unique'):
        with_task_mcp(original, [collision], 'claude-code')
    remote = MCPServerConfig(name='remote', transport='sse', url='http://fixture/sse')
    with pytest.raises(ValueError, match='only stdio'):
        with_task_mcp(original, [remote], 'claude-code')
