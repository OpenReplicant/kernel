from _toy import step
step(lambda d: d.update(passed=d["count"] % 2 == 0, stop=d["count"] >= 3))
