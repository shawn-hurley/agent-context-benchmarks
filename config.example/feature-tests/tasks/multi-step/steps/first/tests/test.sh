#!/bin/bash
set -eu
mkdir -p /logs/verifier
if [ "$(cat /work/answer.txt)" = fixed ]; then printf '1\n'; else printf '0\n'; fi > /logs/verifier/reward.txt
