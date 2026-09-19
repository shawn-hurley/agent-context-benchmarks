"""Exercise asset cancellation and truncated responses using a local HTTP server."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time

from acb.downloads import download_file, download_policy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    records = {}
    for mode in ('trickle', 'stall', 'truncated'):
        started, release, cancelled = threading.Event(), threading.Event(), threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length', '1000000')
                self.end_headers()
                self.wfile.write(b'partial')
                self.wfile.flush()
                started.set()
                if mode == 'truncated':
                    return
                if mode == 'stall':
                    release.wait(60)
                else:
                    while not release.wait(.02):
                        try:
                            self.wfile.write(b'x')
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError):
                            break
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        directory = args.output / mode
        directory.mkdir()
        target = directory / 'asset'
        target.write_bytes(b'original-cache-entry')
        def download():
            with download_policy(cancelled=cancelled):
                download_file(f'http://127.0.0.1:{server.server_port}/asset', target)
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(download)
                assert started.wait(5)
                time.sleep(.1)
                begin = time.monotonic()
                if mode != 'truncated':
                    cancelled.set()
                try:
                    future.result(timeout=45)
                except (RuntimeError, OSError) as error:
                    message = str(error)
                    assert ('incomplete asset download' if mode == 'truncated' else 'cancelled') in message
                else:
                    raise AssertionError('failed transfer was published')
                elapsed = time.monotonic() - begin
            assert target.read_bytes() == b'original-cache-entry'
            assert list(directory.iterdir()) == [target]
            records[mode] = {'passed': True, 'completion_seconds': elapsed,
                             'original_preserved': True, 'staging_files_remaining': 0}
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(5)
    (args.output / 'download-check.json').write_text(json.dumps({'passed': True, 'cases': records}, indent=2))
    print(f'Download checks passed: {args.output}')


if __name__ == '__main__':
    main()
