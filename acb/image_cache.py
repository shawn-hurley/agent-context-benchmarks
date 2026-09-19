"""Provider build snapshots and local image provenance, without dependency locking."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

from acb.harnesses._cache import binary_cache_lock

CONTEXT_LABEL = 'org.acb.build.context-sha256'
RECIPE_LABEL = 'org.acb.build.recipe'


def inspect_image(engine, reference):
    result = subprocess.run([engine, 'image', 'inspect', reference],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        return None
    image = json.loads(result.stdout)[0]
    identity = image.get('Id', '').removeprefix('sha256:')
    if not re.fullmatch(r'[a-f0-9]{64}', identity):
        raise ValueError('provider image has no immutable image identity')
    return {'image_id': 'sha256:' + identity, 'architecture': image.get('Architecture'),
            'os': image.get('Os'), 'labels': image.get('Config', {}).get('Labels') or {}}


def snapshot_context(context, destination):
    inventory = []
    for source in sorted(context.rglob('*')):
        relative = source.relative_to(context)
        if '__pycache__' in relative.parts:
            continue
        if source.is_symlink() or not (source.is_file() or source.is_dir()):
            raise ValueError(f'unsupported provider context entry: {relative}')
        target = destination / relative
        mode = source.stat().st_mode & 0o777
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            inventory.append({'path': relative.as_posix(), 'type': 'directory'})
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            data = source.read_bytes()
            target.write_bytes(data)
            target.chmod(mode)
            inventory.append({'path': relative.as_posix(), 'mode': mode,
                              'sha256': hashlib.sha256(data).hexdigest()})
    return inventory


def prepare_image(context: Path, recipe: str, prefix: str, plan: dict) -> str:
    cache = Path(plan['cache_dir']) / 'provider-images'
    cache.mkdir(parents=True, exist_ok=True)
    engine = plan['environment']
    with tempfile.TemporaryDirectory(prefix='context-', dir=cache) as temporary:
        staging = Path(temporary)
        context_copy = staging / 'context'
        context_copy.mkdir()
        inventory = snapshot_context(context, context_copy)
        recipe_path = Path(recipe)
        if recipe_path.is_absolute() or '..' in recipe_path.parts or not (context_copy / recipe).is_file():
            raise ValueError('provider recipe must be a file within its build context')
        inputs = {'schema': 1, 'recipe': recipe, 'files': inventory}
        digest = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
        reference = prefix + ':' + digest[:20]
        manifest = cache / (digest + '.json')
        with binary_cache_lock(cache, digest):
            image = inspect_image(engine, reference)
            expected_labels = {CONTEXT_LABEL: digest, RECIPE_LABEL: recipe}
            valid = image and all(image['labels'].get(k) == v for k, v in expected_labels.items())
            if image and not valid:
                raise ValueError(f'provider image build provenance mismatch: {reference}')
            previous = None
            if manifest.exists():
                try:
                    previous = json.loads(manifest.read_text())
                except (OSError, ValueError) as error:
                    raise ValueError(f'invalid provider build manifest: {manifest}') from error
                if not isinstance(previous, dict) or previous.get('inputs') != inputs:
                    raise ValueError(f'provider build manifest mismatch: {manifest}')
                if image and previous.get('image') != image:
                    raise ValueError(f'provider cached image changed: {reference}')
            if not image:
                if plan['offline']:
                    raise FileNotFoundError(f'offline: missing verified provider image {reference}')
                command = [engine, 'build', '-f', str(context_copy / recipe), '-t', reference]
                for name, value in expected_labels.items():
                    command.extend(['--label', name + '=' + value])
                subprocess.run([*command, str(context_copy)], check=True, timeout=1800)
                image = inspect_image(engine, reference)
                if not image or any(image['labels'].get(k) != v for k, v in expected_labels.items()):
                    raise ValueError('built provider image did not retain its provenance labels')
            record = {'inputs': inputs, 'image': image, 'reference': reference}
            published = staging / 'manifest.json'
            published.write_text(json.dumps(record, indent=2))
            published.replace(manifest)
            # Avoid a mutable-tag gap between building and freezing providers.
            return image['image_id']
