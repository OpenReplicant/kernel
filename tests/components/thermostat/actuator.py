"""Actuator: applying power raises the current temperature by half a degree per watt."""


def main(inp):
    current, power = inp.data["inputs"]["current"], inp.data["inputs"]["power"]
    return {"data": {"outputs": {"applied": current + 0.5 * power}}}
