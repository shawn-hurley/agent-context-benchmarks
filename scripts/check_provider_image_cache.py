"""Check concurrent provider preparation and offline reuse using a local base image."""
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path

from acb.image_cache import prepare_image


def prepare(arguments):
    context, plan = arguments
    return prepare_image(Path(context), 'Containerfile', 'acb-cache-contract-check', plan)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--engine', default='podman', choices=['podman', 'docker'])
    parser.add_argument('--base', default='localhost/acb-rtk-smoke:0.48.0')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    context = output / 'context'
    context.mkdir()
    (context / 'Containerfile').write_text(f'FROM {args.base}\nCOPY payload /acb-cache-probe\n')
    payload = context / 'payload'
    payload.write_text('provider cache contract fixture\n')
    plan = {'environment': args.engine, 'cache_dir': str(output / 'cache'), 'offline': False}
    with ProcessPoolExecutor(max_workers=2) as pool:
        identities = list(pool.map(prepare, [(str(context), plan)] * 2))
    assert identities[0] == identities[1]
    plan['offline'] = True
    assert prepare((str(context), plan)) == identities[0]
    payload.write_text('changed input must not build offline\n')
    try:
        prepare((str(context), plan))
    except FileNotFoundError:
        pass
    else:
        raise AssertionError('changed inputs accepted offline')
    payload.write_text('provider cache contract fixture\n')
    assert prepare((str(context), plan)) == identities[0]
    manifests = list((output / 'cache/provider-images').glob('*.json'))
    assert len(manifests) == 1
    record = json.loads(manifests[0].read_text())
    assert record['image']['image_id'] == identities[0]
    assert not list((output / 'cache/provider-images').glob('context-*'))
    (output / 'cache-check.json').write_text(json.dumps({
        'passed': True, 'concurrent_image_ids': identities, 'offline_reuse': True,
        'offline_changed_inputs_rejected': True, 'build_record': record}, indent=2))
    print(f'Provider cache check passed: {output}')


if __name__ == '__main__':
    main()
