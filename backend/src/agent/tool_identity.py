"""Host-verifiable identities for effectful agent tool operations."""

from __future__ import annotations

import json
from collections.abc import Mapping
from uuid import UUID, uuid5

from pydantic import JsonValue


_TOOL_OPERATION_NAMESPACE = UUID("a20a3ccb-3bf7-4aac-9dc2-83d21c16dd2e")


def mutation_operation_id(
    run_id: UUID,
    tool_name: str,
    arguments: Mapping[str, JsonValue],
) -> UUID:
    """Derive one stable mutation identity from host-verifiable inputs.

    The sandbox may transport this value, but the host always recomputes it.
    Consequently, changing an arbitrary UUID cannot turn one mutation into a
    fresh operation; a genuinely different canonical request is still bounded
    by the durable per-run mutation budget.
    """

    canonical_arguments = json.dumps(
        arguments,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return uuid5(
        _TOOL_OPERATION_NAMESPACE,
        f"{run_id}:{tool_name}:{canonical_arguments}",
    )
