#!/usr/bin/env python3
"""Retrieve the exact original tool result from this trial's Caveman runtime."""
import sys
from urllib.parse import urlencode
from urllib.request import urlopen

if len(sys.argv) != 2:
    raise SystemExit("usage: acb-recall HANDLE")
with urlopen("http://127.0.0.1:18881/retrieve?" + urlencode({"handle": sys.argv[1]}), timeout=15) as response:
    sys.stdout.buffer.write(response.read())
