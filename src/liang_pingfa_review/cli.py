"""DWG-only public command line interface for the two-stage workflow."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable

from .apply import apply_dwg
from .audit import bound_audit_dwg
from .atomic_output import (
    OutputTargetLeaseSet,
    acquire_new_output_target_leases,
    validate_new_output_targets,
)
from .canonical import canonical_json_bytes, write_new_artifacts
from .contracts import load_artifact, require_fresh_audit
from .errors import ErrorCode, PipelineError
from .local_regression import run_local_regression
from .oda import OdaRunner
from .ownership import OwnershipError, private_staging_capability
from .plan import generate_edit_plan, validate_plan_against_audit
from .reports import render_audit_report, render_plan_review
from .topology_profile import load_topology_profile
from .verify import verify_dwg


def _new_output_paths(*paths: Path, forbidden: tuple[Path, ...] = ()) -> tuple[Path, ...]:
    """Validate new artifact targets and reject aliases to input artifacts."""

    validated = validate_new_output_targets(paths)
    forbidden_keys = {
        os.path.normcase(os.path.abspath(os.fspath(path)))
        for path in forbidden
    }
    if any(
        os.path.normcase(os.path.abspath(os.fspath(path))) in forbidden_keys
        for path in validated
    ):
        raise PipelineError(
            ErrorCode.INVALID_ARGUMENT,
            "artifact output aliases an input or public output",
        )
    return validated


def _bound_new_output_paths(
    *paths: Path,
    forbidden: tuple[Path, ...] = (),
) -> OutputTargetLeaseSet:
    """Acquire artifact-parent leases at command validation time."""

    output_targets = acquire_new_output_target_leases(paths)
    forbidden_keys = {
        os.path.normcase(os.path.abspath(os.fspath(path)))
        for path in forbidden
    }
    try:
        if any(
            os.path.normcase(os.path.abspath(os.fspath(target.destination)))
            in forbidden_keys
            for target in output_targets.targets
        ):
            raise PipelineError(
                ErrorCode.INVALID_ARGUMENT,
                "artifact output aliases an input or public output",
            )
        return output_targets
    except BaseException:
        output_targets.close()
        raise


def _runner(arguments: argparse.Namespace) -> OdaRunner:
    return OdaRunner.discover(arguments.oda_file_converter)


def _emit(event: dict[str, str], *, stream: Any = sys.stdout) -> None:
    """Emit only redacted structured command events."""

    print(json.dumps(event, ensure_ascii=False, separators=(",", ":")), file=stream)


def _doctor(arguments: argparse.Namespace) -> None:
    capability = private_staging_capability()
    try:
        runner = _runner(arguments)
    except PipelineError:
        runner = None
    _emit(
        {
            "status": "ok" if capability.ready and runner is not None else "not_ready",
            "command": "doctor",
            "support_profile": "r2018-ac1032-dxf-exposable-overlay-text",
            "support_profile_readiness": (
                "ready" if capability.ready and runner is not None else "not_ready"
            ),
            # Environment readiness cannot determine whether any user drawing
            # preserves the deliberately narrow profile. Only ``audit`` can.
            "per_file_compatibility": "audit_required",
            "oda_version": runner.version if runner is not None else "unavailable",
            "windows": "ready" if capability.windows else "unsupported",
            "ntfs_private_staging": "supported" if capability.ntfs else "unsupported",
            "dacl": "verified" if capability.dacl else "unsupported",
        }
    )


def _audit(arguments: argparse.Namespace) -> None:
    topology_profile = (
        load_topology_profile(arguments.topology_profile)
        if arguments.topology_profile is not None
        else None
    )
    output_targets = _bound_new_output_paths(
        arguments.audit_out,
        arguments.report_out,
        forbidden=(arguments.input,),
    )
    try:
        # Public artifacts are emitted while the audit's source lexical chain
        # and immutable file handle remain live. A source pathname cannot be
        # redirected between the audit binding and publication transaction.
        with bound_audit_dwg(
            arguments.input,
            _runner(arguments),
            topology_profile=topology_profile,
        ) as audit:
            write_new_artifacts(
                (
                    (
                        output_targets.targets[0].destination,
                        canonical_json_bytes(audit) + b"\n",
                    ),
                    (
                        output_targets.targets[1].destination,
                        render_audit_report(audit).encode("utf-8"),
                    ),
                ),
                existing_parents=tuple(
                    target.parent for target in output_targets.targets
                ),
            )
    finally:
        output_targets.close()
    _emit({"status": "ok", "command": "audit"})


def _plan(arguments: argparse.Namespace) -> None:
    output_targets = _bound_new_output_paths(
        arguments.plan_out,
        arguments.review_out,
        forbidden=(arguments.audit,),
    )
    try:
        audit = load_artifact("audit", arguments.audit)
        plan = generate_edit_plan(audit)
        write_new_artifacts(
            (
                (
                    output_targets.targets[0].destination,
                    canonical_json_bytes(plan) + b"\n",
                ),
                (
                    output_targets.targets[1].destination,
                    render_plan_review().encode("utf-8"),
                ),
            ),
            existing_parents=tuple(
                target.parent for target in output_targets.targets
            ),
        )
    finally:
        output_targets.close()
    _emit({"status": "ok", "command": "plan"})


def _review_plan(arguments: argparse.Namespace) -> None:
    audit = load_artifact("audit", arguments.audit)
    require_fresh_audit(audit)
    plan = load_artifact("plan", arguments.plan)
    validate_plan_against_audit(audit, plan)
    # This summary deliberately omits all handles, text, coordinates, hashes,
    # paths, counts, and source-private metadata.
    print(render_plan_review())


def _apply(arguments: argparse.Namespace) -> None:
    audit = load_artifact("audit", arguments.audit)
    plan = load_artifact("plan", arguments.plan)
    apply_dwg(
        arguments.input,
        audit,
        plan,
        arguments.confirm_plan,
        arguments.output,
        _runner(arguments),
        dry_run=arguments.dry_run,
    )
    _emit({"status": "ok", "command": "apply"})


def _verify(arguments: argparse.Namespace) -> None:
    _new_output_paths(
        arguments.verification_out,
        forbidden=(arguments.input, arguments.audit, arguments.plan),
    )
    audit = load_artifact("audit", arguments.audit)
    plan = load_artifact("plan", arguments.plan)
    verify_dwg(
        arguments.input,
        audit,
        plan,
        _runner(arguments),
        verification_output_path=arguments.verification_out,
    )
    _emit({"status": "ok", "command": "verify"})


def _local_regression(arguments: argparse.Namespace) -> None:
    run_local_regression(
        source_environment_variable=arguments.source_env,
        work_root=arguments.work_root,
        converter=_runner(arguments),
    )
    _emit({"status": "ok", "command": "local-regression"})


def build_parser() -> argparse.ArgumentParser:
    """Build a public parser containing only DWG-oriented commands."""

    parser = argparse.ArgumentParser(
        prog="liang-pingfa-review",
        description=(
            "Fail-closed audit-first DWG review for the R2018/AC1032 "
            "DXF-exposable profile only."
        ),
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    doctor = subcommands.add_parser(
        "doctor",
        help="check profile prerequisites; it does not assess a drawing",
        description="Check profile prerequisites; it does not assess a drawing.",
    )
    doctor.add_argument("--oda-file-converter", type=Path)
    doctor.set_defaults(handler=_doctor)

    audit = subcommands.add_parser(
        "audit", help="assess one DWG for per-file profile compatibility"
    )
    audit.add_argument("--input", type=Path, required=True)
    audit.add_argument("--audit-out", type=Path, required=True)
    audit.add_argument("--report-out", type=Path, required=True)
    audit.add_argument(
        "--topology-profile",
        type=Path,
        help="local read-only beam topology profile; it never authorizes edits",
    )
    audit.add_argument("--oda-file-converter", type=Path)
    audit.set_defaults(handler=_audit)

    plan = subcommands.add_parser("plan", help="generate deterministic edit plan")
    plan.add_argument("--audit", type=Path, required=True)
    plan.add_argument("--plan-out", type=Path, required=True)
    plan.add_argument("--review-out", type=Path, required=True)
    plan.set_defaults(handler=_plan)

    review = subcommands.add_parser("review-plan", help="validate and render a plan")
    review.add_argument("--audit", type=Path, required=True)
    review.add_argument("--plan", type=Path, required=True)
    review.set_defaults(handler=_review_plan)

    apply = subcommands.add_parser(
        "apply", help="apply only an audit-admitted plan"
    )
    apply.add_argument("--input", type=Path, required=True)
    apply.add_argument("--audit", type=Path, required=True)
    apply.add_argument("--plan", type=Path, required=True)
    apply.add_argument("--confirm-plan", required=True)
    apply.add_argument("--output", type=Path, required=True)
    apply.add_argument("--dry-run", action="store_true")
    apply.add_argument("--oda-file-converter", type=Path)
    apply.set_defaults(handler=_apply)

    verify = subcommands.add_parser("verify", help="re-audit an output DWG")
    verify.add_argument("--input", type=Path, required=True)
    verify.add_argument("--audit", type=Path, required=True)
    verify.add_argument("--plan", type=Path, required=True)
    verify.add_argument("--verification-out", type=Path, required=True)
    verify.add_argument("--oda-file-converter", type=Path)
    verify.set_defaults(handler=_verify)

    regression = subcommands.add_parser(
        "local-regression", help="run disposable local-only DWG regression"
    )
    regression.add_argument("--source-env", default="LIANG_PINGFA_LOCAL_DWG")
    regression.add_argument("--work-root", type=Path, required=True)
    regression.add_argument("--oda-file-converter", type=Path)
    regression.set_defaults(handler=_local_regression)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run a command and render only redacted pipeline error codes."""

    parser = build_parser()
    arguments = parser.parse_args(argv)
    handler: Callable[[argparse.Namespace], None] = arguments.handler
    try:
        handler(arguments)
    except PipelineError as error:
        _emit(error.redacted_event(), stream=sys.stderr)
        return 1
    except (OSError, OwnershipError, ValueError):
        _emit({"status": "error", "code": ErrorCode.INTERNAL_ERROR.value}, stream=sys.stderr)
        return 1
    return 0
