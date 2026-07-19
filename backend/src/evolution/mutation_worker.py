"""Separate-sandbox worker for four strict local-model mutation requests."""

from __future__ import annotations

import sys
from uuid import uuid5

from vital_relay.evolution.ace.model_client import (
    LocalOpenAIModelClient,
    ModelClientError,
    ModelClientFailureCode,
)
from vital_relay.evolution.hashing import canonical_sha256
from vital_relay.evolution.mutation_contracts import (
    MAX_MUTATION_SANDBOX_REQUEST_BYTES,
    MUTATION_MODEL_SCHEMA_NAME,
    MUTATION_SYSTEM_PROMPT,
    MutationModelOutput,
    MutationSandboxRequest,
    MutationSandboxResponse,
    MutationWorkerProposalRecord,
    MutationWorkerProposalStatus,
    MutationWorkerResult,
    proposal_payload,
)


_MALFORMED_RESPONSE_CODES = frozenset(
    {
        ModelClientFailureCode.INVALID_STRUCTURED_JSON,
        ModelClientFailureCode.SCHEMA_MISMATCH,
    }
)


class _MutationWorkerRound:
    """Make exactly four independent requests without constructing candidates."""

    def __init__(self, client: LocalOpenAIModelClient) -> None:
        self._client = client

    def run(self, request: MutationSandboxRequest) -> MutationWorkerResult:
        payload = proposal_payload(request)
        records: list[MutationWorkerProposalRecord] = []
        for slot, seed in enumerate(request.budget.seeds):
            attempt_id = uuid5(request.round_id, f"proposal-attempt:{slot}")
            binding = self._client.create_request_binding(
                seed=seed,
                budget_slot=slot,
                candidate_budget=request.budget.candidate_budget,
                input_budget_bytes=request.budget.input_budget_bytes,
                output_budget_tokens=request.budget.output_budget_tokens,
                system_prompt=MUTATION_SYSTEM_PROMPT,
                response_model=MutationModelOutput,
                schema_name=MUTATION_MODEL_SCHEMA_NAME,
            )
            request_sha256 = self._client.bound_request_sha256(
                binding=binding,
                system_prompt=MUTATION_SYSTEM_PROMPT,
                user_payload=payload,
                response_model=MutationModelOutput,
            )
            try:
                completion = self._client.complete_bound_json(
                    binding=binding,
                    system_prompt=MUTATION_SYSTEM_PROMPT,
                    user_payload=payload,
                    response_model=MutationModelOutput,
                )
            except ModelClientError as exc:
                records.append(
                    MutationWorkerProposalRecord.create(
                        round_id=request.round_id,
                        attempt_id=attempt_id,
                        request_binding=binding,
                        request_sha256=exc.request_sha256 or request_sha256,
                        response_sha256=exc.response_sha256,
                        response_bytes=None,
                        model_output=None,
                        status=(
                            MutationWorkerProposalStatus.INVALID_RESPONSE
                            if exc.category in _MALFORMED_RESPONSE_CODES
                            else MutationWorkerProposalStatus.REQUEST_FAILED
                        ),
                        failure_code=exc.category,
                    )
                )
                continue
            records.append(
                MutationWorkerProposalRecord.create(
                    round_id=request.round_id,
                    attempt_id=attempt_id,
                    request_binding=binding,
                    request_sha256=completion.request_sha256,
                    response_sha256=completion.response_sha256,
                    response_bytes=completion.response_bytes,
                    model_output=completion.output,
                    status=MutationWorkerProposalStatus.PROPOSED,
                    failure_code=None,
                )
            )
        return MutationWorkerResult.create(
            sandbox=request.sandbox,
            runtime_authorization_sha256=request.runtime_authorization_sha256,
            runtime_identity_sha256=request.runtime_identity_sha256,
            source_manifest_sha256=request.source_manifest_sha256,
            sandbox_request_sha256=request.request_sha256,
            round_id=request.round_id,
            parent_candidate_sha256=request.parent.candidate_sha256,
            source_report_sha256=request.source_report_sha256,
            failure_packet_sha256=canonical_sha256(request.failure_packet),
            attempted_at=request.created_at,
            budget=request.budget,
            proposals=tuple(records),
            complete=all(
                record.status is not MutationWorkerProposalStatus.REQUEST_FAILED
                for record in records
            ),
        )


def main() -> int:
    """Consume one bounded request and emit one bounded worker result."""

    try:
        raw = sys.stdin.buffer.read(MAX_MUTATION_SANDBOX_REQUEST_BYTES + 1)
        request = MutationSandboxRequest.from_wire_bytes(raw)
        with LocalOpenAIModelClient(request.local_model) as client:
            result = _MutationWorkerRound(client).run(request)
        response = MutationSandboxResponse(
            sandbox=request.sandbox,
            runtime_authorization_sha256=request.runtime_authorization_sha256,
            runtime_identity_sha256=request.runtime_identity_sha256,
            source_manifest_sha256=request.source_manifest_sha256,
            request_sha256=request.request_sha256,
            result=result,
        ).to_wire_bytes()
        sys.stdout.buffer.write(response)
        sys.stdout.buffer.flush()
        return 0
    except BaseException:
        # Never expose credentials, prompts, protected inputs, or model output
        # through stderr or a secondary diagnostic envelope.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
