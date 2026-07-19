"""Production entrypoint for one preregistered DGM experiment."""

from __future__ import annotations

import argparse
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from stat import S_ISREG
import sys

from pydantic import SecretStr

from vital_relay.agent.contracts import SandboxKind
from vital_relay.agent.sandbox import SubprocessSandboxCommandExecutor
from vital_relay.evolution.ace.model_client import LocalModelConfig
from vital_relay.evolution.bundle_store import CandidateBundleStore
from vital_relay.evolution.bundles import CandidateArtifactAttestationAuthority
from vital_relay.evolution.contracts import EvaluationReport, PartitionName
from vital_relay.evolution.dgm import (
    DGMAuthorizedMutationRunnerFactory,
    DGMClaimStatus,
    DGMDevelopmentRunnerIdentity,
    DGMExperimentPlan,
    DGMExperimentRunner,
)
from vital_relay.evolution.dgm_store import FilesystemDGMExperimentStore
from vital_relay.evolution.evaluator import (
    HostIntegrityAuthority,
    conclusion_validator_artifact_bytes,
)
from vital_relay.evolution.hashing import canonical_json_bytes
from vital_relay.evolution.mutation import MutationRuntimeAuthorizationAuthority


_MAX_EVALUATION_RESPONSE_BYTES = 16_000_000


class _ProcessSignedReportProvider:
    """Invoke a pinned external process and accept only host-signed reports."""

    def __init__(
        self,
        executable: Path,
        identity: DGMDevelopmentRunnerIdentity,
    ) -> None:
        executable = executable.resolve(strict=True)
        metadata = executable.stat(follow_symlinks=False)
        if executable.is_symlink() or not S_ISREG(metadata.st_mode):
            raise ValueError("evaluation runner must be a regular non-symlink file")
        if not executable.is_absolute() or not executable.stat().st_mode & 0o111:
            raise ValueError("evaluation runner must be an absolute executable")
        if sha256(executable.read_bytes()).hexdigest() != identity.runner_sha256:
            raise ValueError("evaluation runner bytes do not match preregistration")
        if identity.sandbox is SandboxKind.IN_PROCESS:
            raise ValueError("evaluation runner must use Docker or NemoClaw")
        self._executable = executable
        self._identity = identity
        self._executor = SubprocessSandboxCommandExecutor()

    @property
    def identity(self) -> DGMDevelopmentRunnerIdentity:
        return self._identity

    @property
    def process_isolated(self) -> bool:
        return True

    def evaluate(self, bundle, *, partition: PartitionName) -> EvaluationReport:
        if partition is PartitionName.FINAL_TEST:
            raise ValueError("DGM CLI never requests final-test data")
        request = canonical_json_bytes(
            {
                "schema_version": 1,
                "contract": "dgm_signed_evaluation_request_v1",
                "partition": partition,
                "runner_identity": self._identity.model_dump(mode="json"),
                "bundle": bundle.model_dump(mode="json"),
            }
        )
        completed = self._executor.run(
            (str(self._executable),),
            input_bytes=request,
            timeout_seconds=(
                self._identity.limits.timeout_seconds_per_case
                * self._identity.limits.max_cases
            ),
        )
        if (
            completed.returncode != 0
            or not completed.stdout
            or len(completed.stdout) > _MAX_EVALUATION_RESPONSE_BYTES
        ):
            raise RuntimeError("process evaluation runner failed")
        return EvaluationReport.model_validate_json(completed.stdout)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m vital_relay.evolution.dgm_cli",
        description="Run one real bounded DGM inherited-improver experiment.",
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--root-report", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--bundle-artifact-root", type=Path, required=True)
    parser.add_argument("--experiment-artifact-root", type=Path, required=True)
    parser.add_argument("--protected-artifact-root", type=Path, required=True)
    parser.add_argument("--allowed-conclusions", type=Path, required=True)
    parser.add_argument("--started-at", type=_aware_datetime, required=True)

    for name in ("artifact", "integrity", "runtime", "store"):
        parser.add_argument(f"--{name}-key-file", type=Path, required=True)
        parser.add_argument(f"--{name}-key-id", required=True)
    parser.add_argument(
        "--expected-protected-artifacts-sha256",
        required=True,
    )

    parser.add_argument(
        "--mutation-sandbox",
        choices=(SandboxKind.DOCKER.value, SandboxKind.NEMOCLAW.value),
        required=True,
    )
    parser.add_argument("--docker-image-reference")
    parser.add_argument("--docker-model-network")
    parser.add_argument("--nemoclaw-sandbox-name")
    parser.add_argument("--nemoclaw-sandbox-identity-sha256")
    parser.add_argument("--nemoclaw-policy-sha256")
    parser.add_argument("--nemoclaw-runtime-sha256")

    parser.add_argument("--model-base-url", required=True)
    parser.add_argument("--model-provider", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--model-artifact-sha256", required=True)
    parser.add_argument("--model-api-key-file", type=Path, required=True)
    parser.add_argument("--model-timeout-seconds", type=float, required=True)
    parser.add_argument("--model-max-tokens", type=int, required=True)
    parser.add_argument("--mutation-timeout-seconds", type=float, default=120.0)

    parser.add_argument(
        "--evaluation-sandbox",
        choices=(SandboxKind.DOCKER.value, SandboxKind.NEMOCLAW.value),
        required=True,
    )
    parser.add_argument("--evaluation-runner", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        plan = DGMExperimentPlan.model_validate_json(_safe_read(args.plan))
        if args.evaluation_sandbox != plan.development_runner.sandbox.value:
            raise ValueError("evaluation sandbox differs from the canonical plan")
        conclusions = _allowed_conclusions(args.allowed_conclusions)
        protected_artifacts = _artifact_tree(args.protected_artifact_root)
        validator_name = "evolution/conclusion-allowlist.json"
        validator_bytes = conclusion_validator_artifact_bytes(conclusions)
        existing_validator = protected_artifacts.get(validator_name)
        if existing_validator not in {None, validator_bytes}:
            raise ValueError("protected conclusion allowlist was substituted")
        protected_artifacts[validator_name] = validator_bytes

        integrity_authority = HostIntegrityAuthority(
            _key(args.integrity_key_file),
            key_id=args.integrity_key_id,
            expected_protected_artifacts_sha256=(
                args.expected_protected_artifacts_sha256
            ),
            protected_artifacts=protected_artifacts,
            allowed_conclusion_summaries=conclusions,
        )
        artifact_authority = CandidateArtifactAttestationAuthority(
            _key(args.artifact_key_file),
            key_id=args.artifact_key_id,
        )
        project_root = args.project_root.resolve(strict=True)
        runtime_authority = MutationRuntimeAuthorizationAuthority(
            _key(args.runtime_key_file),
            key_id=args.runtime_key_id,
            project_root=project_root,
        )
        runtime_authorization = _authorize_runtime(args, runtime_authority)
        local_model = LocalModelConfig(
            base_url=args.model_base_url,
            provider=args.model_provider,
            model=args.model_id,
            revision=args.model_revision,
            artifact_sha256=args.model_artifact_sha256,
            api_key=SecretStr(
                _safe_read(args.model_api_key_file).decode("utf-8").strip()
            ),
            timeout_seconds=args.model_timeout_seconds,
            max_tokens=args.model_max_tokens,
            temperature=0.0,
            max_retries=0,
        )
        mutation_factory = DGMAuthorizedMutationRunnerFactory(
            integrity_authority=integrity_authority,
            artifact_authority=artifact_authority,
            runtime_authority=runtime_authority,
            runtime_authorization=runtime_authorization,
            local_model=local_model,
            budget=plan.mutation_budget,
            sandbox_timeout_seconds=args.mutation_timeout_seconds,
        )
        bundle_store = CandidateBundleStore(
            args.bundle_artifact_root,
            artifact_authority,
        )
        experiment_store = FilesystemDGMExperimentStore(
            args.experiment_artifact_root,
            _key(args.store_key_file),
            key_id=args.store_key_id,
            bundle_store=bundle_store,
            integrity_authority=integrity_authority,
        )
        report_provider = _ProcessSignedReportProvider(
            args.evaluation_runner,
            plan.development_runner,
        )
        runner = DGMExperimentRunner(
            plan=plan,
            integrity_authority=integrity_authority,
            artifact_authority=artifact_authority,
            mutation_factory=mutation_factory,
            report_provider=report_provider,
            bundle_store=bundle_store,
            experiment_store=experiment_store,
        )
        root_bundle = bundle_store.get(plan.root_bundle_sha256)
        root_report = EvaluationReport.model_validate_json(
            _safe_read(args.root_report)
        )
        result = runner.run(
            root_bundle=root_bundle,
            root_development_report=root_report,
            started_at=args.started_at,
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "reason": type(exc).__name__,
                    "message": str(exc),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2

    print(
        json.dumps(
            {
                "status": result.claim.status.value,
                "experiment_id": str(result.plan.experiment_id),
                "claim_sha256": result.claim.claim_sha256,
                "result_sha256": result.result_sha256,
            },
            sort_keys=True,
        )
    )
    if (
        result.claim.status
        is not DGMClaimStatus.RECURSIVE_IMPROVEMENT_DEMONSTRATED
    ):
        return 3
    return 0


def _authorize_runtime(args, authority: MutationRuntimeAuthorizationAuthority):
    sandbox = SandboxKind(args.mutation_sandbox)
    if sandbox is SandboxKind.DOCKER:
        if not args.docker_image_reference or not args.docker_model_network:
            raise ValueError("Docker mutation requires image and model network")
        if any(
            value is not None
            for value in (
                args.nemoclaw_sandbox_name,
                args.nemoclaw_sandbox_identity_sha256,
                args.nemoclaw_policy_sha256,
                args.nemoclaw_runtime_sha256,
            )
        ):
            raise ValueError("Docker mutation cannot include NemoClaw identity")
        return authority.authorize_docker(
            image_reference=args.docker_image_reference,
            model_network=args.docker_model_network,
        )
    if any(
        value is None
        for value in (
            args.nemoclaw_sandbox_name,
            args.nemoclaw_sandbox_identity_sha256,
            args.nemoclaw_policy_sha256,
            args.nemoclaw_runtime_sha256,
        )
    ):
        raise ValueError("NemoClaw mutation requires its exact reviewed identity")
    if args.docker_image_reference or args.docker_model_network:
        raise ValueError("NemoClaw mutation cannot include Docker identity")
    return authority.authorize_nemoclaw(
        sandbox_name=args.nemoclaw_sandbox_name,
        sandbox_identity_sha256=args.nemoclaw_sandbox_identity_sha256,
        policy_sha256=args.nemoclaw_policy_sha256,
        runtime_sha256=args.nemoclaw_runtime_sha256,
    )


def _key(path: Path) -> bytes:
    value = _safe_read(path)
    if len(value) < 32:
        raise ValueError(f"authority key is too short: {path.name}")
    return value


def _safe_read(path: Path) -> bytes:
    path = path.resolve(strict=True)
    metadata = path.stat(follow_symlinks=False)
    if path.is_symlink() or not S_ISREG(metadata.st_mode):
        raise ValueError(f"required file is unsafe: {path.name}")
    return path.read_bytes()


def _allowed_conclusions(path: Path) -> tuple[str, ...]:
    value = json.loads(_safe_read(path))
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("allowed conclusions must be a JSON string array")
    return tuple(value)


def _artifact_tree(root: Path) -> dict[str, bytes]:
    root = root.resolve(strict=True)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("protected artifact root is unsafe")
    artifacts: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("protected artifact tree contains a symlink")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("protected artifact tree contains an unsafe entry")
        artifacts[path.relative_to(root).as_posix()] = path.read_bytes()
    if not artifacts:
        raise ValueError("protected artifact root is empty")
    return artifacts


def _aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("--started-at must include a timezone")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
