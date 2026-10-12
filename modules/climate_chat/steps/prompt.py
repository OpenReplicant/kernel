"""climate_chat: a person's message {text, by} -> a model call asking what setpoint they mean.

The rooms the entity knows are recalled into the prompt, so the model picks one of them.
"""
import os

from kernel import db

SYSTEM = ("You turn a person's request about room temperature into JSON. Reply with JSON only, no prose: "
          '{"room": "<one of the known rooms>", "celsius": <number>} when they ask for a temperature in a '
          'known room, or {"room": null} when they don\'t.')


def Process(msg, metadata, msgType, dataType, vars=None, globals=None):
    with db.connect(os.environ.get("DATABASE_URL")) as conn:
        rooms = [r[0] for r in conn.execute("MATCH (r:room) RETURN r.key ORDER BY r.key").fetchall()]
    prompt = f"Known rooms: {', '.join(rooms) or '(none)'}\nRequest: {msg['text']}"
    call = {"role": "interpreter", "system": SYSTEM, "prompt": prompt,
            "inputs": {"message": msg, "rooms": rooms}}
    return {"msg": {"model_call": call, "rooms": rooms}, "metadata": metadata, "msgType": msgType}
