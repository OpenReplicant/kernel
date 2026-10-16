"""A demo service for the devops demo (milestone 2): answers GET / with simulated work whose latency
grows with the load on this replica (about 20 requests/s each before it saturates), and exposes
Prometheus metrics at /metrics. More replicas share the load, so scaling out brings latency down.
Standard library only."""
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CAPACITY = 20.0                  # requests per second one replica handles comfortably
lock = threading.Lock()
arrivals: deque = deque()
stats = {"requests": 0, "seconds": 0.0}


def latency() -> float:
    now = time.monotonic()
    with lock:
        arrivals.append(now)
        while arrivals and arrivals[0] < now - 5:
            arrivals.popleft()
        rate = len(arrivals) / 5.0
    return min(2.0, 0.01 / max(0.005, 1 - rate / CAPACITY))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics":
            with lock:
                body = (f"# TYPE demo_requests_total counter\ndemo_requests_total {stats['requests']}\n"
                        f"# TYPE demo_request_seconds_sum counter\ndemo_request_seconds_sum {stats['seconds']:.6f}\n"
                        f"# TYPE demo_request_seconds_count counter\ndemo_request_seconds_count {stats['requests']}\n")
        else:
            took = latency()
            time.sleep(took)
            with lock:
                stats["requests"] += 1
                stats["seconds"] += took
            body = "ok\n"
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
