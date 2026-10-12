"""A room-climate simulator: the world the climate entity senses and acts on (S2).

Not part of the entity. Each room has a temperature and a heater. Every `tick` seconds the
temperature rises by `heat` with the heater on and falls by `loss` with it off, and the
simulator streams one reading per room to the entity's perceiver chain (RuleGo's synchronous
execute API, one at a time, so readings arrive in order; a failed post is a lost reading). The entity acts back through a small HTTP API:

    POST /heater  {"room": "lounge", "on": true}  -> {"room", "heater", "at"}
    GET  /state                                   -> {"rooms": {...}, "switches": [...]}

Run: python world/climate_sim.py --rooms lounge=18 --port 9191 [--tick 0.5]
     (RULEGO_URL, default http://127.0.0.1:9090; --chain, default climate_sense_reading)
"""
import argparse
import json
import os
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class World:
    def __init__(self, rooms: dict[str, float], heat: float, loss: float):
        self.lock = threading.Lock()
        self.rooms = {r: {"celsius": t, "heater": False} for r, t in rooms.items()}
        self.heat, self.loss = heat, loss
        self.switches = []  # every heater command received, in order
        self.seq = 0

    def step(self) -> list[dict]:
        with self.lock:
            self.seq += 1
            readings = []
            for room, s in self.rooms.items():
                s["celsius"] += self.heat if s["heater"] else -self.loss
                readings.append({"sensor": f"{room}-thermometer", "room": room, "celsius": round(s["celsius"], 2),
                                 "heater": s["heater"], "at": now(), "seq": self.seq})
            return readings

    def switch(self, room: str, on: bool) -> dict:
        with self.lock:
            if room not in self.rooms:
                raise KeyError(room)
            self.rooms[room]["heater"] = bool(on)
            event = {"room": room, "heater": bool(on), "at": now()}
            self.switches.append(event)
            return event

    def state(self) -> dict:
        with self.lock:
            return {"rooms": json.loads(json.dumps(self.rooms)), "switches": list(self.switches), "seq": self.seq}


def stream(world: World, url: str, tick: float, stop: threading.Event):
    session = requests.Session()
    while not stop.wait(tick):
        for reading in world.step():
            try:
                session.post(url, json=reading, timeout=10)
            except requests.RequestException as e:  # a stream: a lost reading is just lost
                print(json.dumps({"lost": reading["seq"], "error": str(e)[:200]}), flush=True)


def handler(world: World):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._send(200, world.state()) if self.path == "/state" else self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/heater":
                return self._send(404, {"error": "not found"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                self._send(200, world.switch(body["room"], body["on"]))
            except (KeyError, ValueError) as e:
                self._send(400, {"error": f"bad request: {e}"})

        def log_message(self, *args):
            pass

    return H


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--rooms", default="lounge=18", help="room=celsius,...")
    p.add_argument("--port", type=int, default=9191)
    p.add_argument("--tick", type=float, default=0.5)
    p.add_argument("--heat", type=float, default=0.4)
    p.add_argument("--loss", type=float, default=0.1)
    p.add_argument("--chain", default="climate_sense_reading")
    a = p.parse_args()
    rooms = {r.split("=")[0]: float(r.split("=")[1]) for r in a.rooms.split(",")}
    world = World(rooms, a.heat, a.loss)
    url = f"{os.environ.get('RULEGO_URL', 'http://127.0.0.1:9090')}/api/v1/rules/{a.chain}/execute/READING"
    stop = threading.Event()
    threading.Thread(target=stream, args=(world, url, a.tick, stop), daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", a.port), handler(world))
    print(json.dumps({"simulator": "up", "port": a.port, "rooms": rooms, "perceiver": url}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
