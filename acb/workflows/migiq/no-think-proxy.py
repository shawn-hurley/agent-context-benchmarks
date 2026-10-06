"""Local-only OpenAI-compatible pass-through for the Kantra Qwen pilot.

Praxis remains the upstream, so every request and response keeps its normal
measurement path. Chat requests gain Qwen's nonthinking template setting and
expose only Goose's text editing tools for this local pilot.
"""
from __future__ import annotations

from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os

LISTEN_PORT = int(os.environ.get("ACB_PROXY_PORT", "18879"))
PRAXIS_PORT = int(os.environ.get("ACB_PRAXIS_PORT", "18880"))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def forward(self):
        size = int(self.headers.get("Content-Length", "0"))
        if size > 8 * 1024 * 1024:
            self.send_error(413)
            return
        body = self.rfile.read(size) if size else b""
        if self.command == "POST" and self.path.startswith("/v1/chat/completions"):
            try:
                request = json.loads(body)
                if not isinstance(request, dict):
                    raise ValueError("expected JSON object")
                settings = request.setdefault("chat_template_kwargs", {})
                settings["enable_thinking"] = False
                if isinstance(request.get("tools"), list):
                    request["tools"] = [tool for tool in request["tools"]
                                        if tool.get("function", {}).get("name") in {"shell", "write", "edit"}]
                body = json.dumps(request, separators=(",", ":")).encode()
            except (ValueError, TypeError):
                self.send_error(400, "invalid chat request")
                return
        headers = {key: value for key, value in self.headers.items()
                   if key.lower() not in ("host", "connection", "content-length", "transfer-encoding")}
        if body:
            headers["Content-Length"] = str(len(body))
        upstream = HTTPConnection("127.0.0.1", PRAXIS_PORT, timeout=360)
        try:
            upstream.request(self.command, self.path, body=body, headers=headers)
            response = upstream.getresponse()
            self.send_response_only(response.status, response.reason)
            for key, value in response.getheaders():
                if key.lower() not in ("connection", "content-length", "transfer-encoding"):
                    self.send_header(key, value)
            self.send_header("Connection", "close")
            self.end_headers()
            while chunk := response.read1(65536):
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.close_connection = True
            upstream.close()


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", LISTEN_PORT), Handler).serve_forever()
