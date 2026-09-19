#!/usr/bin/env python3
"""Goose shell adapter retaining explicit process status for eligible logs."""
import json
import os
import re
import signal
import subprocess
import sys
import tempfile

# Preserve login-shell probes, arguments, stdin, cwd and environment.
inner_shell = os.environ.get("ACB_CAVEMAN_INNER_SHELL", "/bin/bash")
if len(sys.argv) < 3 or sys.argv[1] != '-c' or re.search(r'\bacb-recall\b', sys.argv[2]):
    os.execv('/bin/bash', ['/bin/bash', *sys.argv[1:]])
with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as error:
    process = subprocess.Popen([inner_shell, *sys.argv[1:]], stdout=output, stderr=error)

    def forward(signum, frame):
        if process.poll() is None:
            process.send_signal(signum)

    signal.signal(signal.SIGTERM, forward)
    signal.signal(signal.SIGINT, forward)
    status = process.wait()
    size = output.tell()
    errors = error.tell()
    output.seek(0)
    error.seek(0)
    encoded = None
    if status == 0 and not errors and 2048 <= size <= 64 * 1024 * 1024:
        raw = output.read()
        try:
            text = raw.decode('utf-8')
            lines = [line for line in text.splitlines() if line.strip()]
            if (len(lines) >= 8
                and not re.search(r'(error|exception|fail(?:ed|ure)?|traceback|panic|fatal)', text, re.I)
                and all(re.match(r'^(?:\d{4}-\d\d-\d\d[T ][\d:.+Z-]+\s+)?\[?INFO\]?\s', line) for line in lines)):
                encoded = json.dumps({'acb_caveman': 1, 'exit_code': 0, 'stdout': text}).encode()
        except UnicodeDecodeError:
            pass
        output.seek(0)
    if encoded is not None:
        sys.stdout.buffer.write(encoded)
    else:
        while chunk := output.read(65536):
            sys.stdout.buffer.write(chunk)
    while chunk := error.read(65536):
        sys.stderr.buffer.write(chunk)
sys.exit(status if status >= 0 else 128 - status)
