from _toy import step
step(lambda d: d.update(count=d.get("count", 0) + 1))
