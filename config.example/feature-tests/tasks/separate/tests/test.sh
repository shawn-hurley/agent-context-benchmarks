#!/bin/bash
set -eu
mkdir -p /logs/verifier
test ! -f /work/answer.txt
test -z "${ANTHROPIC_API_KEY:-}"
printf '1\n' > /logs/verifier/reward.txt
