"""Deterministic OpenAI fixture: ordinary Bash calls, then inspect returned output.

Runs inside an isolated, network-disabled test container. It never contacts a model.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess

ROOT = Path("/testbed")
ROOT.mkdir(exist_ok=True)
subprocess.run(["git", "init", "-q", str(ROOT)], check=True)
for index in range(120):
    (ROOT / f"tracked-fixture-{index:03d}.txt").write_text("fixture\n")
subprocess.run(["git", "-C", str(ROOT), "add", "--", *[f"tracked-fixture-{index:03d}.txt" for index in range(120)]], check=True)
subprocess.run(["git", "-C", str(ROOT), "-c", "user.name=ACB Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "RTK test fixture"], check=True)
for index in range(120):
    (ROOT / f"tracked-fixture-{index:03d}.txt").write_text("modified fixture\n")

COMMANDS = [
    "git status",
    "printf 'ACB_UNSUPPORTED\\n'",
    "printf 'ACB_ONCE\\n' >> /tmp/acb-rtk-once; printf 'ACB_STDOUT\\n'; printf 'ACB_STDERR\\n' >&2; exit 7",
]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"data":[]}')

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with Path("/tmp/acb-rtk-requests.jsonl").open("a") as log:
            log.write(json.dumps(body) + "\n")
        if self.path.split("?")[0].endswith("/count_tokens"):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"input_tokens":100}')
            return
        if self.path.split("?")[0].endswith("/messages"):
            self.anthropic(body)
            return
        messages = body.get("messages", [])
        tool_results = [message for message in messages if message.get("role") == "tool"]
        shell_name = next((tool.get("function", {}).get("name") for tool in body.get("tools", [])
                           if tool.get("function", {}).get("name") in ("bash", "developer__shell", "shell")), None)
        index = len(tool_results)
        delta = {"role": "assistant", "content": "Fixture complete."}
        finish = "stop"
        if shell_name and index < len(COMMANDS):
            delta = {"role": "assistant", "tool_calls": [{
                "index": 0, "id": f"fixture-call-{index}", "type": "function",
                "function": {"name": shell_name, "arguments": json.dumps({"command": COMMANDS[index], "description": "RTK fixture command"})},
            }]}
            finish = "tool_calls"
        usage = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream" if body.get("stream") else "application/json")
        self.end_headers()
        if not body.get("stream"):
            self.wfile.write(json.dumps({"id": "fixture", "object": "chat.completion", "created": 0, "model": "fixture-model", "choices": [{"index": 0, "message": delta, "finish_reason": finish}], "usage": usage}).encode())
            return
        for choice in [{"index": 0, "delta": delta, "finish_reason": None}, {"index": 0, "delta": {}, "finish_reason": finish}]:
            payload = {"id": "fixture", "object": "chat.completion.chunk", "created": 0, "model": "fixture-model", "choices": [choice]}
            self.wfile.write(("data: " + json.dumps(payload) + "\n\n").encode())
        self.wfile.write(("data: " + json.dumps({"id": "fixture", "object": "chat.completion.chunk", "choices": [], "usage": usage}) + "\n\ndata: [DONE]\n\n").encode())
        self.wfile.flush()

    def log_message(self, *args):
        pass

    def anthropic(self, body):
        results = [block for message in body.get("messages", []) for block in message.get("content", [])
                   if isinstance(block, dict) and block.get("type") == "tool_result"]
        has_bash = any(tool.get("name") == "Bash" for tool in body.get("tools", []))
        index = len(results)
        block = {"type": "text", "text": "Fixture complete."}
        stop = "end_turn"
        if has_bash and index < len(COMMANDS):
            block = {"type": "tool_use", "id": f"fixture-call-{index}", "name": "Bash",
                     "input": {"command": COMMANDS[index], "description": "RTK fixture command"}}
            stop = "tool_use"
        message = {"id": "msg_fixture", "type": "message", "role": "assistant", "model": body.get("model"),
                   "content": [block], "stop_reason": stop, "stop_sequence": None,
                   "usage": {"input_tokens": 100, "output_tokens": 20}}
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream" if body.get("stream") else "application/json")
        self.end_headers()
        if not body.get("stream"):
            self.wfile.write(json.dumps(message).encode())
            return
        events = [("message_start", {"message": {**message, "content": [], "stop_reason": None, "usage": {"input_tokens": 100, "output_tokens": 0}}}),
                  ("content_block_start", {"index": 0, "content_block": {**block, **({"input": {}} if stop == "tool_use" else {"text": ""})}}),
                  ("content_block_delta", {"index": 0, "delta": {"type": "input_json_delta", "partial_json": json.dumps(block["input"])} if stop == "tool_use" else {"type": "text_delta", "text": block["text"]}}),
                  ("content_block_stop", {"index": 0}),
                  ("message_delta", {"delta": {"stop_reason": stop, "stop_sequence": None}, "usage": {"output_tokens": 20}}),
                  ("message_stop", {})]
        for name, event in events:
            self.wfile.write((f"event: {name}\ndata: " + json.dumps({"type": name, **event}) + "\n\n").encode())
        self.wfile.flush()


ThreadingHTTPServer(("127.0.0.1", 18080), Handler).serve_forever()
