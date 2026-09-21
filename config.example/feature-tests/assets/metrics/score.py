import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('-i', required=True)
parser.add_argument('-o', required=True)
args = parser.parse_args()
rows = [json.loads(line) for line in Path(args.i).read_text().splitlines() if line.strip()]
values = [row['quality'] for row in rows if row and isinstance(row.get('quality'), (int, float))]
Path(args.o).write_text(json.dumps({'quality_sum': sum(values), 'graded_attempts': len(values)}))
