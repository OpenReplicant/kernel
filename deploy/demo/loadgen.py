"""Load for the devops demo: DEMO_RATE requests per second to http://shop:8000/, spread over the shop's
replicas (a fresh DNS lookup per request). Standard library only."""
import os
import random
import socket
import threading
import time
import urllib.request

RATE = float(os.environ.get("DEMO_RATE", "5"))


def hit() -> None:
    try:
        ip = random.choice(sorted({a[4][0] for a in socket.getaddrinfo("shop", 8000, proto=socket.IPPROTO_TCP)}))
        urllib.request.urlopen(f"http://{ip}:8000/", timeout=5).read()
    except Exception:
        pass


while True:
    threading.Thread(target=hit, daemon=True).start()
    time.sleep(1 / RATE if RATE > 0 else 1)
