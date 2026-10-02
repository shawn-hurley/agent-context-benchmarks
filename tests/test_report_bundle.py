from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile

from acb.cli import main
from acb.report_bundle import write_bundle
from test_comparison import run
from test_html_rendering import enrich


class References(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.references = []
        self.ids = set()
        self.feed(content)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if 'id' in attrs:
            self.ids.add(attrs['id'])
        for field in ('href', 'src'):
            if field in attrs:
                self.references.append(attrs[field])


def assert_portable_links(root):
    documents = {path.resolve(): References(path.read_text()) for path in root.rglob('*.html')}
    for path, document in documents.items():
        for reference in document.references:
            link = urlsplit(reference)
            assert not link.scheme and not link.netloc, reference
            target = (path.parent / unquote(link.path)).resolve() if link.path else path
            assert target.is_relative_to(root.resolve()), reference
            assert target.is_file(), reference
            if link.fragment:
                assert unquote(link.fragment) in documents[target].ids, reference


def test_bundle_remains_readable_after_runs_removed_and_archive_relocated(tmp_path):
    roots = [enrich(run(tmp_path / name, {'passed': 1, 'failed': 0})) for name in ('baseline', 'candidate')]
    trial = roots[0] / 'passed'
    (trial / 'config.json').write_text('{"api_key": "DO-NOT-EXPORT-STARTUP-SECRET"}')
    (trial / 'praxis.yaml').write_text('token: DO-NOT-EXPORT-PRAXIS-SECRET')
    outside = tmp_path / 'outside.log'
    outside.write_text('DO-NOT-EXPORT-OUTSIDE-TRIAL')
    (trial / 'trial.log').symlink_to(outside)
    report = json.loads((roots[0] / 'report.json').read_text())
    report['evaluations'][0]['api_key'] = 'DO-NOT-EXPORT-STRUCTURED-SECRET'
    (roots[0] / 'report.json').write_text(json.dumps(report))
    archive = write_bundle(roots, tmp_path / 'report.zip')
    for root in roots:
        shutil.rmtree(root)
    moved = tmp_path / 'moved'
    moved.mkdir()
    shutil.move(archive, moved / 'report.zip')
    extracted = moved / 'extracted'
    with ZipFile(moved / 'report.zip') as bundle:
        bundle.extractall(extracted)
    assert_portable_links(extracted)
    assert (extracted / 'index.html').is_file()
    assert (extracted / 'index.comparison.json').is_file()
    assert (extracted / 'index-benchmarks' / 'chart.umd.min.js').is_file()
    assert (extracted / 'index-benchmarks' / 'Chart.js.LICENSE.md').is_file()
    texts = '\n'.join(path.read_text() for path in extracted.rglob('*') if path.is_file())
    assert 'DO-NOT-EXPORT-' not in texts
    assert '+ patch for passed' in texts
    assert '[redacted]' in texts
    assert 'Back to overview' in texts and 'Next benchmark' in texts


def test_cli_bundle_without_html_option(tmp_path, capsys):
    root = run(tmp_path / 'run', {'task': 1})
    archive = tmp_path / 'single.zip'
    main(['report', str(root), '--bundle', str(archive)])
    assert 'report bundle:' in capsys.readouterr().out
    with ZipFile(archive) as bundle:
        assert 'index.html' in bundle.namelist()
        assert 'index.comparison.json' not in bundle.namelist()


def test_comparison_controls_do_not_make_missing_measurements_comparable(tmp_path):
    from acb.comparison_html import write_reports
    baseline = run(tmp_path / 'baseline', {'task': 1, 'missing': None})
    candidate = run(tmp_path / 'candidate', {'task': 0, 'missing': None}, complete=False)
    page = write_reports([baseline, candidate], tmp_path / 'comparison.html').read_text()
    assert 'data-filter="quality"' in page and 'Regressions' in page
    assert 'data-filter="outcome"' in page and 'data-filter="harness"' in page
    assert 'data-sort="candidateGrade"' in page
    assert 'data-quality="worse"' in page and 'data-quality="unknown"' in page
    assert 'data-incomplete="true"' in page
    assert 'Comparable token totals unavailable' in page
    assert 'Not comparable' in page
    assert 'bar-fill' not in page.split('</style>', 1)[1]
