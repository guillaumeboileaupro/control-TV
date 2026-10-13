"""JSON values at the process boundaries (the bridge's stdio lines, the embedded runtime).

A request is read into these types and narrowed by `isinstance` checks before use; nothing
from outside is assumed to have a shape it was not checked to have.
"""

from __future__ import annotations

from typing import TypeAlias

JsonValue: TypeAlias = bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"] | None
JsonObject: TypeAlias = dict[str, JsonValue]


def as_object(value: JsonValue) -> JsonObject:
    """`value` when it is a JSON object, else an empty one: a missing part is not guessed."""
    return value if isinstance(value, dict) else {}


def as_list(value: JsonValue) -> list[JsonValue]:
    """`value` when it is a JSON array, else an empty one."""
    return value if isinstance(value, list) else []
