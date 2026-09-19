"""Controlled multi-step extension lifecycle and accounting checks (no paid model)."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys

from acb.harbor.dataset import prepare_dataset
from acb.preparation import prepare_assets, freeze_provider_images


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('combined',type=Path)
    parser.add_argument('output',type=Path)
    parser.add_argument('--mode',choices=['success','early-stop','later-timeout'],default='success')
    args=parser.parse_args()
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    task=output/'tasks/bridge';shutil.copytree(args.combined/'tasks/bridge',task)
    original=(task/'tests/test.sh').read_text()
    for step in ['first','second']:
        folder=task/'steps'/step;folder.mkdir(parents=True)
        shutil.copytree(task/'tests',folder/'tests')
        (folder/'instruction.md').write_text('Complete the controlled fixture. Step '+step+'.')
    with (task/'task.toml').open('a') as f:
        f.write('\n[[steps]]\nname = "first"\nmin_reward = 1\n[steps.agent]\ntimeout_sec = 90\n[[steps]]\nname = "second"\n[steps.agent]\ntimeout_sec = '+('5' if args.mode=='later-timeout' else '90')+'\n')
    if args.mode=='early-stop':
        (task/'steps/first/tests/test.sh').write_text('mkdir -p /logs/verifier; echo 0 > /logs/verifier/reward.txt\n')
    if args.mode=='later-timeout':
        server=task/'environment/server.py';s=server.read_text();s=s.replace('def command_for(body, index):','def command_for(body, index):\n    if index == 0 and "Step second." in json.dumps(body):\n        return "sleep 600"')
        server.write_text(s)
    plan=json.loads((args.combined/'prepared.json').read_text())
    for key in ['task_plans','runtime_contracts','verifier_contracts','provider_images','manifest']:
        plan.pop(key,None)
    plan['benchmark_config']['path']=str(task.parent)
    plan['manifest']=prepare_dataset(plan)
    plan['manifest']['metrics']=[{'type':'sum','kwargs':{}}]
    plan['max_workers']=1
    for h in plan['harnesses'].values():h['timeout']=120
    if args.mode!='success':plan['harnesses']={'pi':plan['harnesses']['pi']}
    path=output/'prepared.json'
    def worker(action,dest):
        path.write_text(json.dumps(plan,indent=2))
        with (output/(action+'.log')).open('w') as log:
            return subprocess.run([sys.executable,'-m','acb.harbor.worker',action,str(path),str(dest)],stdout=log,stderr=subprocess.STDOUT).returncode
    assert worker('inspect',output/'inspection')==0
    records=list((output/'inspection/harbor').glob('*/agent/acb/runtime.json'))
    runtime=json.loads(records[0].read_text())
    plan['runtime_contracts']={'bridge':{k:v for k,v in runtime.items() if k not in ('task_id','harness_version','launch_profile')}}
    plan['verifier_contracts']=json.loads((output/'inspection/verifier-contracts.json').read_text())
    plan=freeze_provider_images(prepare_assets(plan))
    code=worker('run',output/'measured')
    checks={}
    for name in plan['harnesses']:
        report=json.loads((output/'measured'/name/'report.json').read_text())
        record=report['evaluations'][0]
        steps=record['step_results']
        assert len(steps)==(1 if args.mode=='early-stop' else 2),steps
        usage=[json.loads(l) for l in (output/'measured'/name/'usage.jsonl').read_text().splitlines()]
        assert len({r['request_id'] for r in usage})==len(usage)
        assert [r['turn_index'] for r in usage]==list(range(len(usage)))
        assert {r['step_name'] for r in usage} <= {'first','second'}
        if args.mode=='success':
            assert report['resolved']==1 and report['incomplete_measurements']==0,report
            assert {r['step_name'] for r in usage}=={'first','second'}
            for step in ['first','second']:
                artifacts=output/'measured'/name/'instances'/record['trial_id']/'steps'/step
                for integration in ['rtk','caveman']:
                    manifest=json.loads((artifacts/'integrations'/integration/'manifest.json').read_text())
                    assert manifest['verification']['agent_tool_verified'],manifest
        elif args.mode=='later-timeout':
            assert record['failed_step']=='second' and record['status']=='error',record
            assert report['incomplete_measurements']==1
        else:
            assert record['resolved'] is False
        checks[name]={'steps':len(steps),'requests':len(usage),'status':record['status'],'measurement_complete':record['measurement_complete']}
    (output/'check.json').write_text(json.dumps({'mode':args.mode,'checks':checks,'worker_exit':code},indent=2))
    print(json.dumps(checks),flush=True)

if __name__=='__main__':main()
