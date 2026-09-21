#!/bin/bash
set -eu
mkdir -p /logs/verifier
if [ "$(cat /work/answer.txt)" = fixed ]; then value=1; else value=0; fi
printf '{"quality":%s,"coverage":0.5}\n' "$value" > /logs/verifier/reward.json
