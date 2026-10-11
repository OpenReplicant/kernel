"""Sensor: reports the temperature it is given (the task's start, or what the actuator applied)."""


def main(inp):
    sample = inp.data["inputs"]["sample"]
    return {"data": {"outputs": {"reading": sample if isinstance(sample, (int, float)) else sample["temp"]}}}
