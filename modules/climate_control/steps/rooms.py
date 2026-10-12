"""climate_control: the rooms to control, as items for the `for` node."""
import os

from kernel import db
from kernel.nodes.graph_read import read


def Process(msg, metadata, msgType, dataType, vars=None, globals=None):
    with db.connect(os.environ.get("DATABASE_URL")) as conn:
        rows = read(conn, "MATCH (r:room) RETURN r.key ORDER BY r.key")
    return {"msg": {"rooms": [{"room": key} for (key,) in rows]}, "metadata": metadata, "msgType": msgType}
