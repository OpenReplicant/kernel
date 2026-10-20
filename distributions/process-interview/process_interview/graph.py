"""The interview in Cognee: messages kept verbatim, the maps as typed nodes, each relation quoting its message.

One Cognee dataset per interview (or organisation). Every node has a stable id from the dataset and its
key on the map, so naming a post again in a later answer adds to the same node. Each relation is a Cognee
edge whose properties say who said it, where and when, in their own words (`quote`, `message`, `said_by`,
`at`), so every line of a map can be traced to a sentence a person said.
"""
from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_OID, uuid5

from cognee.infrastructure.engine import DataPoint, Edge

from .vocab import RELATIONS


class Message(DataPoint):
    text: str
    author: str
    time: str
    conversation: str
    metadata: dict[str, Any] = {"index_fields": ["text"]}


class Thing(DataPoint):
    """Something on the maps. `kind` is one of vocab.KINDS; `related` holds its outgoing relations."""
    name: str
    kind: str
    key: str
    said_in: list[Message] = []
    related: list[tuple[Edge, "Thing"]] = []
    metadata: dict[str, Any] = {"index_fields": ["name"]}


def node_id(dataset: str, key: str):
    return uuid5(NAMESPACE_OID, f"{dataset}|{key}")


def points(dataset: str, message: dict, statements: list[dict]) -> list[DataPoint]:
    """The nodes and edges one answer adds: its message, and each statement as an edge quoting it."""
    msg = Message(id=node_id(dataset, f"message:{message['conversation']}:{message['id']}"), text=message["text"],
                  author=message["author"], time=message["time"], conversation=message["conversation"])
    things: dict[str, Thing] = {}

    def thing(key, kind, name):
        if key not in things:
            things[key] = Thing(id=node_id(dataset, key), name=name, kind=kind, key=key, said_in=[msg])
        return things[key]

    for s in statements:
        src, dst = thing(s["s"], s["subject_kind"], s["subject"]), thing(s["o"], s["object_kind"], s["object"])
        src.related.append((Edge(relationship_type=s["relation"], edge_text=f"{s['subject']} {s['relation'].replace('_', ' ')} {s['object']}: “{s['quote']}”",
                                 properties={"quote": s["quote"], "message": str(msg.id), "said_by": message["author"],
                                             "at": message["time"], "term": RELATIONS[s["relation"]][2]}), dst))
    return [msg, *things.values()]


async def read(dataset: str) -> tuple[dict[str, dict], list[dict]]:
    """(entities by key, statements) as stored in the dataset's graph."""
    from cognee.context_global_variables import set_database_global_context_variables
    from cognee.infrastructure.databases.graph import get_graph_engine
    from cognee.modules.data.methods.get_datasets_by_name import get_datasets_by_name
    from cognee.modules.users.methods import get_default_user

    user = await get_default_user()
    found = await get_datasets_by_name(dataset, user.id)
    if not found:
        return {}, []
    async with set_database_global_context_variables(found[0].id, user.id):
        nodes, edges = await (await get_graph_engine()).get_graph_data()
    things = {str(i): p for i, p in nodes if p.get("type") == "Thing"}
    entities = {p["key"]: {"kind": p["kind"], "name": p["name"]} for p in things.values()}
    statements = []
    for src, dst, rel, p in edges:
        if str(src) in things and str(dst) in things and rel in RELATIONS:
            a, b = things[str(src)], things[str(dst)]
            props = p.get("properties") or {}
            statements.append({"s": a["key"], "subject": a["name"], "subject_kind": a["kind"], "relation": rel,
                               "o": b["key"], "object": b["name"], "object_kind": b["kind"], **props})
    return entities, statements
