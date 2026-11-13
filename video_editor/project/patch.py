"""The difference between two JSON documents, and applying it.

diff(a, b) is what turns a into b; apply(a, diff(a, b)) == b exactly. An
operation's undo is diff(after, before) and its redo diff(before, after)
(ops.py's `restore`), so both are exact whatever the operation did, and both
stay small: only what changed is in them.

A patch node is a JSON list:
  ["="  , value]             replace with value
  ["-"]                      remove this key (only inside a "d" node)
  ["d"  , {key: node}]       a dict: each named key patched, the rest left
  ["l"  , order, {id: node}] a list of dicts that each have a unique "id":
                             the ids in their new order (null when the ids
                             and their order are unchanged), and a node for
                             each new or changed entry. An id missing from
                             `order` is removed.
None means "no difference" and is only ever the top-level answer.
"""

from __future__ import annotations


class PatchError(ValueError):
    """A patch that doesn't fit the document it is applied to."""


def _ids(items) -> list | None:
    """The ids of a list of dicts that each have a unique string "id"."""
    ids = []
    for entry in items:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            return None
        ids.append(entry["id"])
    return ids if ids and len(set(ids)) == len(ids) else None


def diff(a, b, strict: bool = False):
    """The patch that turns a into b, or None when they are equal.

    Python counts 1, 1.0 and True as equal, and 0.0 and -0.0, and a document
    must come back with the very values it had. `strict` looks inside equal
    values for a number that is written differently (another kind, or the
    other zero); without it the comparison is the fast built-in one, and the
    caller checks the result (ops.apply does, and asks again with strict
    when the bytes didn't come back)."""
    if type(a) is type(b) and a == b and (not strict or _same_types(a, b)):
        return None
    if isinstance(a, dict) and isinstance(b, dict):
        out = {}
        for key, old in a.items():
            if key not in b:
                out[key] = ["-"]
            else:
                node = diff(old, b[key], strict)
                if node is not None:
                    out[key] = node
        for key, new in b.items():
            if key not in a:
                out[key] = ["=", new]
        return ["d", out]
    if isinstance(a, list) and isinstance(b, list):
        ids_a, ids_b = _ids(a), _ids(b)
        if ids_a is not None and ids_b is not None:
            old = {entry["id"]: entry for entry in a}
            nodes = {}
            for entry in b:
                before = old.get(entry["id"])
                node = ["=", entry] if before is None else diff(before, entry, strict)
                if node is not None:
                    nodes[entry["id"]] = node
            return ["l", None if ids_a == ids_b else ids_b, nodes]
    return ["=", b]


def _same_types(a, b) -> bool:
    """Whether two equal values are written the same throughout: every
    number of the same kind, and no 0.0 where the other has -0.0."""
    if isinstance(a, dict):
        return all(_same_types(v, b[k]) for k, v in a.items())
    if isinstance(a, list):
        return all(_same_types(x, y) for x, y in zip(a, b))
    if type(a) is not type(b):
        return False
    return not isinstance(a, float) or repr(a) == repr(b)


def apply(value, node):
    """`value` with the patch applied. The input is never changed; parts the
    patch doesn't touch are shared with it."""
    if node is None:
        return value
    if not isinstance(node, list) or not node:
        raise PatchError("a patch node is a list")
    kind = node[0]
    if kind == "=" and len(node) == 2:
        return node[1]
    if kind == "d" and len(node) == 2 and isinstance(node[1], dict):
        if not isinstance(value, dict):
            raise PatchError("the patch expects an object here")
        out = dict(value)
        for key, sub in node[1].items():
            if sub == ["-"]:
                if key not in out:
                    raise PatchError(f"nothing to remove at {key!r}")
                del out[key]
            elif isinstance(sub, list) and sub and sub[0] == "=":
                out[key] = apply(None, sub)
            else:
                if key not in out:
                    raise PatchError(f"nothing to change at {key!r}")
                out[key] = apply(out[key], sub)
        return out
    if kind == "l" and len(node) == 3 and isinstance(node[2], dict):
        ids = _ids(value) if isinstance(value, list) else None
        if ids is None and value != []:
            raise PatchError("the patch expects a list of entries with ids here")
        old = {entry["id"]: entry for entry in value}
        order = node[1] if node[1] is not None else list(old)
        if not isinstance(order, list):
            raise PatchError("a list patch's order is a list of ids")
        out = []
        for entry_id in order:
            sub = node[2].get(entry_id)
            if entry_id in old:
                out.append(apply(old[entry_id], sub))
            elif isinstance(sub, list) and sub and sub[0] == "=":
                out.append(apply(None, sub))
            else:
                raise PatchError(f"no entry {entry_id!r} to keep or change")
        return out
    raise PatchError(f"unknown patch node {kind!r}")
