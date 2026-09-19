#!/bin/bash
set -eu
mkdir -p /logs/verifier
if [ "$(cat /work/answer.txt)" = fixed ]; then
    printf '1\n' > /logs/verifier/reward.txt
else
    printf '0\n' > /logs/verifier/reward.txt
fi
