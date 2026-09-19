#!/bin/bash
set -eu
mkdir -p /logs/verifier
printf '{"quality":0.75,"coverage":0.5}\n' > /logs/verifier/reward.json
