"""Render matched Kantra violations without the large unmatched-rules listing."""
import sys
from pathlib import Path

import yaml

rulesets = yaml.safe_load(Path(sys.argv[1]).read_text())
if not isinstance(rulesets, list):
    raise SystemExit("Kantra output must be a list of rulesets")
count = 0
for ruleset in rulesets:
    violations = ruleset.get("violations") or {}
    if not isinstance(violations, dict):
        raise SystemExit("Kantra violations must be a mapping")
    for rule_id, violation in violations.items():
        if not isinstance(violation, dict):
            raise SystemExit("Kantra violation must be a mapping")
        count += 1
        print(f"## {rule_id} ({ruleset.get('name', 'unknown')})")
        print(f"{violation.get('description', '')} [{violation.get('category', '')}]")
        incidents = [item for item in (violation.get("incidents") or []) if isinstance(item, dict)]
        print(f"Incidents: {len(incidents)} (full details in output.yaml)")
        locations = list(dict.fromkeys(str(item.get("uri", "")).removeprefix("file:///work/")
                                       for item in incidents if item.get("uri")))
        for location in locations[:4]:
            print(f"- {location}")
        if incidents:
            message = " ".join(str(incidents[0].get("message", "")).split())
            print(f"Advice: {message[:160]}")
        print()
if not count:
    raise SystemExit("Kantra found no matched violations")
print(f"Matched violations: {count}")
