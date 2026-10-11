"""Controller: heat until the target is reached, then report done."""


def main(inp):
    reading, target = inp.data["inputs"]["input"], inp.data["params"]["target"]
    if reading >= target:
        return {"data": {"outputs": {"done": {"temp": reading}}}}
    return {"data": {"outputs": {"command": 1.0}}}
