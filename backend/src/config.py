"""Fail-closed host configuration for reviewed Generator context."""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from vital_relay.agent.contracts import VLLMSettings
from vital_relay.evolution.ace.contracts import (
    ACERole,
    ModelIdentity,
    Playbook,
    RoleIdentity,
    SourcePartition,
)
from vital_relay.evolution.ace.selection import (
    GENERATOR_CONTEXT_MAX_CHARACTERS,
    GENERATOR_CONTEXT_MAX_ITEMS,
    GeneratorContextSelector,
)
from vital_relay.evolution.hashing import canonical_sha256


PROJECT_ROOT = Path(__file__).resolve().parents[3]
BASELINE_PLAYBOOK_PATH = (
    PROJECT_ROOT / "agents/playbooks/baseline/playbook.yaml"
)
BASELINE_PLAYBOOK_DIGEST_PATH = (
    PROJECT_ROOT / "agents/playbooks/baseline/playbook.sha256"
)
AGENT_MODEL_REVISION_ENV = "VITAL_RELAY_VLLM_MODEL_REVISION"
AGENT_MODEL_ARTIFACT_SHA256_ENV = "VITAL_RELAY_VLLM_MODEL_ARTIFACT_SHA256"
GENERATOR_ROLE_VERSION = "1.0.0"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_GENERATOR_SELECTION_CONFIGURATION = {
    "adapted_playbook": None,
    "available_tools_source": "active_policy",
    "fallback": "reviewed_baseline_only",
    "max_characters": GENERATOR_CONTEXT_MAX_CHARACTERS,
    "max_items": GENERATOR_CONTEXT_MAX_ITEMS,
    "renderer": "canonical_operational_tactics_v1",
    "selector": "host_verified_playbook_v1",
}
GENERATOR_ROLE_CONFIGURATION_SHA256 = canonical_sha256(
    _GENERATOR_SELECTION_CONFIGURATION
)


class GeneratorContextConfigurationError(ValueError):
    """The reviewed Generator identity or baseline could not be verified."""


def load_pinned_baseline_playbook(
    playbook_path: Path = BASELINE_PLAYBOOK_PATH,
    digest_path: Path = BASELINE_PLAYBOOK_DIGEST_PATH,
) -> Playbook:
    """Load one self-hashed baseline bound to its detached digest and review."""

    try:
        raw_digest = digest_path.read_text(encoding="utf-8")
        raw_playbook = playbook_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GeneratorContextConfigurationError(
            "reviewed baseline context is unavailable"
        ) from exc

    digest_parts = raw_digest.strip().split()
    if (
        len(digest_parts) != 2
        or _SHA256_PATTERN.fullmatch(digest_parts[0]) is None
        or digest_parts[1] != playbook_path.name
    ):
        raise GeneratorContextConfigurationError(
            "reviewed baseline digest sidecar is invalid"
        )
    try:
        playbook = Playbook.model_validate(yaml.safe_load(raw_playbook))
    except (TypeError, ValueError, ValidationError, yaml.YAMLError) as exc:
        raise GeneratorContextConfigurationError(
            "reviewed baseline playbook failed verification"
        ) from exc
    if (
        playbook.playbook_sha256 != digest_parts[0]
        or playbook.provenance.source_partition
        is not SourcePartition.REVIEWED_BASELINE
        or playbook.review_manifest is None
    ):
        raise GeneratorContextConfigurationError(
            "reviewed baseline digest or review binding does not match"
        )
    return playbook


def generator_context_selector_from_environment(
    settings: VLLMSettings,
    *,
    provider: str = "vllm",
) -> GeneratorContextSelector:
    """Build the production selector from required model provenance metadata."""

    return build_generator_context_selector(
        settings,
        provider=provider,
        revision=_required_environment_setting(AGENT_MODEL_REVISION_ENV),
        artifact_sha256=_required_sha256_setting(
            AGENT_MODEL_ARTIFACT_SHA256_ENV
        ),
    )


def build_generator_context_selector(
    settings: VLLMSettings,
    *,
    provider: str = "vllm",
    revision: str,
    artifact_sha256: str,
    playbook_path: Path = BASELINE_PLAYBOOK_PATH,
    digest_path: Path = BASELINE_PLAYBOOK_DIGEST_PATH,
) -> GeneratorContextSelector:
    """Bind the reviewed baseline and exact non-secret vLLM configuration."""

    validated_settings = VLLMSettings.model_validate(settings)
    clean_revision = _clean_required_value(revision, "model revision")
    if _SHA256_PATTERN.fullmatch(artifact_sha256) is None:
        raise GeneratorContextConfigurationError(
            "model artifact SHA-256 must be 64 lowercase hexadecimal characters"
        )
    inference_configuration = validated_settings.model_dump(
        mode="json",
        exclude={"api_key"},
    )
    try:
        model_identity = ModelIdentity.create(
            provider=provider,
            model_id=validated_settings.model,
            revision=clean_revision,
            artifact_sha256=artifact_sha256,
            inference_config_sha256=canonical_sha256(
                inference_configuration
            ),
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise GeneratorContextConfigurationError(
            "model provenance metadata is invalid"
        ) from exc
    return GeneratorContextSelector(
        baseline_playbook=load_pinned_baseline_playbook(
            playbook_path,
            digest_path,
        ),
        adapted_playbook=None,
        generator_role=RoleIdentity.create(
            role=ACERole.GENERATOR,
            version=GENERATOR_ROLE_VERSION,
            configuration_sha256=GENERATOR_ROLE_CONFIGURATION_SHA256,
        ),
        model_identity=model_identity,
    )


def _required_environment_setting(name: str) -> str:
    raw_value = os.environ.get(name)
    if raw_value is None:
        raise GeneratorContextConfigurationError(
            f"{name} is required when VITAL_RELAY_AGENT_ENABLED=true"
        )
    return _clean_required_value(raw_value, name)


def _required_sha256_setting(name: str) -> str:
    value = _required_environment_setting(name)
    if _SHA256_PATTERN.fullmatch(value) is None:
        raise GeneratorContextConfigurationError(
            f"{name} must be 64 lowercase hexadecimal characters"
        )
    return value


def _clean_required_value(value: str, label: str) -> str:
    if not value or value != value.strip():
        raise GeneratorContextConfigurationError(
            f"{label} must be a non-empty clean value"
        )
    return value


__all__ = (
    "AGENT_MODEL_ARTIFACT_SHA256_ENV",
    "AGENT_MODEL_REVISION_ENV",
    "BASELINE_PLAYBOOK_DIGEST_PATH",
    "BASELINE_PLAYBOOK_PATH",
    "GENERATOR_ROLE_CONFIGURATION_SHA256",
    "GENERATOR_ROLE_VERSION",
    "GeneratorContextConfigurationError",
    "build_generator_context_selector",
    "generator_context_selector_from_environment",
    "load_pinned_baseline_playbook",
)
