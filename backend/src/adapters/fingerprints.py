"""Canonical fingerprints shared by every idempotency adapter."""

import hashlib
import json

from pydantic import BaseModel


def model_fingerprint(model: BaseModel) -> str:
    payload = model.model_dump(mode="json")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
