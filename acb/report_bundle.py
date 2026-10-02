"""Portable report evidence and ZIP exports, without copying whole run directories."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile

from acb.html_components import ARTIFACT_BUDGET, MAX_ARTIFACTS, PREVIEW_BYTES, escape, preview
from acb.provenance import requested_config


class EvidenceExport:
    """Save report evidence beside a detail page using relative links."""

    def __init__(self, directory, href):
        self.directory, self.href = Path(directory), href

    def trial(self, trial_id):
        name = hashlib.sha256(trial_id.encode()).hexdigest()[:16]
        return EvidenceExport(self.directory / name, self.href + '/' + name)

    def write_text(self, name, content):
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('invalid report evidence path')
        target = self.directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
        from urllib.parse import quote
        return self.href + '/' + quote(relative.as_posix())

    def trial_evidence(self, trial):
        document = requested_config({'evaluation': trial['evaluation'], 'trajectory': trial['trajectory']})
        href = self.write_text('trial-evidence.json.txt', json.dumps(document, indent=2, ensure_ascii=False))
        return '<p><a href="' + escape(href) + '">Saved trial evidence</a></p>'

    def artifact_sections(self, directory):
        if directory is None or not directory.is_dir():
            return ''
        root = directory.resolve()
        remaining, count = ARTIFACT_BUDGET, 0
        body = ''
        allowed = {'transcript.jsonl', 'trial.log', 'exception.txt', 'grader.log',
                   'scarfbench_eval_batch.log', 'agent.out', 'agent.err', 'build.log', 'test.log'}
        for folder, dirs, names in os.walk(directory, followlinks=False):
            dirs[:] = sorted(name for name in dirs if name not in
                             {'benchmark', 'scarfbench-eval', 'target', '.git', 'node_modules', 'bin'})
            for name in sorted(names):
                path = Path(folder) / name
                relative = path.relative_to(directory)
                if name not in allowed and not ('validation' in relative.parts and path.suffix in {'.log', '.txt', '.out', '.err'}):
                    continue
                if not path.is_file() or not path.resolve().is_relative_to(root):
                    continue
                if count >= MAX_ARTIFACTS or remaining <= 0:
                    return body + '<p class="muted">Additional log previews omitted.</p>'
                limit = min(PREVIEW_BYTES, remaining)
                try:
                    with path.open('rb') as stream:
                        content = stream.read(limit + 1)
                except OSError:
                    continue
                truncated = len(content) > limit
                text = content[:limit].decode('utf-8', errors='replace')
                if truncated:
                    text += '\n[Preview truncated; the original run retains the full log.]\n'
                href = self.write_text('logs/' + relative.as_posix() + '.txt', text)
                body += ('<details><summary>' + escape(relative) + '</summary><a href="' +
                         escape(href) + '">Saved log preview</a>' + preview(text) + '</details>')
                remaining -= min(len(content), limit)
                count += 1
        return body


def write_bundle(run_dirs, destination):
    """Write an archive that opens from index.html without a server or network."""
    from acb.comparison_html import write_reports
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='acb-report-bundle-') as temporary:
        root = Path(temporary)
        write_reports(run_dirs, root / 'index.html')
        (root / 'README.txt').write_text(
            'Extract this archive and open index.html in a browser.\n'
            'Charts, navigation, source changes, and saved evidence work offline.\n'
            'Saved log previews are bounded; complete original logs remain in the run directory.\n'
            'Startup configuration is omitted; structured credential fields are redacted.\n'
            'Saved logs and source changes retain their application content.\n')
        handle, archive_name = tempfile.mkstemp(prefix='.acb-report-', suffix='.zip', dir=destination.parent)
        os.close(handle)
        archive = Path(archive_name)
        try:
            with ZipFile(archive, 'w', compression=ZIP_DEFLATED) as bundle:
                for path in sorted(root.rglob('*')):
                    if path.is_file():
                        bundle.write(path, path.relative_to(root).as_posix())
            archive.replace(destination)
        finally:
            archive.unlink(missing_ok=True)
    return destination
