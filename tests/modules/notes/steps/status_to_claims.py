"""notes: turn a status command {note, status, embedding?} into a write for graph_write.

The command itself is the source; the status is stated by whoever sent it.
"""


def Process(msg, metadata, msgType, dataType, vars=None, globals=None):
    note = {"ref": "n", "label": "note", "key": msg["note"]}
    if "embedding" in msg:
        note["props"] = {"embedding": msg["embedding"]}
    write = {
        "source": {"kind": "command", "content": msg, "uri": "chain:notes_set_status"},
        "things": [note],
        "claims": [{"subject": "n", "predicate": "notes.status", "value": msg["status"],
                    "method": "stated", "confidence": 0.9, "span": "$.status", "replaces": "current"}],
    }
    return {"msg": write, "metadata": metadata, "msgType": msgType}
