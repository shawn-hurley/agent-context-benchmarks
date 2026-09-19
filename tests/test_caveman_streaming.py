"""Real sockets verify opaque SSE forwarding and cancellation during silence."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import threading

import pytest

from test_caveman_gateway import gateway


@pytest.mark.parametrize('cancel', [False, True])
def test_stream_bytes_and_quiet_upstream_cancellation(monkeypatch, cancel):
    first = b'data: {"id":"tool-7","cache_control":{"type":"ephemeral"}}\n\n'
    last = b'data: [DONE]\n\n'
    finish = threading.Event()
    closed = threading.Event()

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            assert self.path == '/v1/chat/completions?fixture=1'
            assert self.headers['Authorization'] == 'Bearer fixture'
            assert self.rfile.read(int(self.headers['Content-Length'])) == b'{"stream":true}'
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('X-Request-ID', 'fixture-id')
            self.end_headers()
            self.wfile.write(first)
            self.wfile.flush()
            if cancel:
                self.connection.settimeout(4)
                if self.connection.recv(1) == b'':
                    closed.set()
            else:
                assert finish.wait(4)
                self.wfile.write(last)
                self.wfile.flush()

    upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
    monkeypatch.setattr(gateway, 'UPSTREAM_PORT', upstream.server_port)
    proxy = ThreadingHTTPServer(('127.0.0.1', 0), gateway.Handler)
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (upstream, proxy)]
    for thread in threads:
        thread.start()
    client = socket.create_connection(('127.0.0.1', proxy.server_port), timeout=4)
    client.settimeout(4)
    try:
        body = b'{"stream":true}'
        client.sendall(b'POST /v1/chat/completions?fixture=1 HTTP/1.1\r\nHost: fixture\r\nAuthorization: Bearer fixture\r\nContent-Length: ' +
                       str(len(body)).encode() + b'\r\n\r\n' + body)
        received = b''
        while first not in received:
            received += client.recv(65536)
        headers, payload = received.split(b'\r\n\r\n', 1)
        assert b'X-Request-ID: fixture-id' in headers
        assert payload == first  # First event arrived while upstream is still waiting.
        if cancel:
            client.close()
            assert closed.wait(3), 'quiet upstream remained open after client cancellation'
        else:
            finish.set()
            while chunk := client.recv(65536):
                payload += chunk
            assert payload == first + last
    finally:
        finish.set()
        client.close()
        for server in (proxy, upstream):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)
