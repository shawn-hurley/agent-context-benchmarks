"""Observed agent turns and tool interactions, separate from model requests."""
import json
from pathlib import Path


def trajectory(path: Path, harness: str):
    if not path.exists():
        return {'turns': None, 'tool_calls': None, 'events': [], 'definition': None}
    events=[]
    for line in path.read_text(errors='replace').splitlines():
        try:
            item=json.loads(line)
            if isinstance(item,dict): events.append(item)
        except ValueError:
            continue  # Harness startup diagnostics can precede the event stream.
    turns=set(); tools={}; recognized=False
    for index,event in enumerate(events):
        kind=event.get('type');message=event.get('message') or {}
        if not isinstance(message,dict): message={}
        if harness=='pi':
            if kind=='turn_start': turns.add(index);recognized=True
            if kind=='tool_execution_start':
                tools[event.get('toolCallId',str(index))]=event
        elif harness=='opencode':
            part=event.get('part') or {}
            if kind=='step_start':turns.add(part.get('messageID',index));recognized=True
            if kind=='tool_use':tools[part.get('callID',str(index))]=event
        elif harness in ('goose','claude-code'):
            if message.get('role')=='assistant' and message.get('id'):
                turns.add(message['id']);recognized=True
            for content in message.get('content',[]) if isinstance(message.get('content'),list) else []:
                if isinstance(content,dict) and content.get('type') in ('tool_use','toolRequest'):
                    ident=content.get('id') or content.get('toolCallId')
                    if ident:tools[ident]=content
    return {'turns':len(turns) if recognized else None,
            'tool_calls':len(tools) if recognized else None, 'events':events,
            'definition':{'pi':'turn_start events','opencode':'distinct step-start message IDs',
                          'goose':'distinct assistant message IDs','claude-code':'distinct assistant message IDs'}.get(harness) if recognized else None}
