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
    ArtifactPublication,
    OutputTargetLeaseSet,
    acquire_new_output_target_leases,
    publish_artifacts,
    validate_new_output_targets,
)
from .canonical import (
    CanonicalJsonError,
    canonical_json_bytes,
    normalize_json_value,
    write_new_artifacts,
)
from .contracts import load_artifact, require_fresh_audit
from .errors import ErrorCode, PipelineError
from .local_regression import run_local_regression
from .native_apply import native_apply as apply_native_dwg
from .native_audit import bound_native_audit
from .native_bridge import (
    consume_native_session,
    native_doctor_status,
    prepare_native_session,
    write_private_native_session_descriptor,
)
from .native_contracts import (
    load_native_artifact,
    load_native_config,
)
from .native_plan import (
    generate_native_plan,
    validate_native_plan_against_audit,
)
from .native_verify import verify_native_published_output
from .oda import OdaRunner
from .ownership import OwnershipError, private_staging_capability
from .plan import generate_edit_plan, validate_plan_against_audit
from .reports import (
    render_audit_report,
    render_native_audit_report,
    render_native_plan_review,
    render_plan_review,
)
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

    # CLI events are also generated JSON. Normalize first so an accidental
    # future nested event cannot bypass the shared fixed-depth policy.
    print(
        json.dumps(
            normalize_json_value(event),
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        file=stream,
    )


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


def _native_config_path(arguments: argparse.Namespace, *, required: bool) -> Path | None:
    """Use only one explicit option or one documented environment variable."""

    supplied = getattr(arguments, "native_config", None)
    configured = supplied or os.environ.get("LIANG_PINGFA_NATIVE_CONFIG")
    if configured is None:
        if required:
            raise PipelineError(
                ErrorCode.NATIVE_CONFIG_INVALID,
                "native config must be explicit or set by environment",
            )
        return None
    return Path(configured)


def _native_config(arguments: argparse.Namespace) -> dict[str, Any]:
    path = _native_config_path(arguments, required=True)
    assert path is not None
    return load_native_config(path)


def _native_session_prepare(arguments: argparse.Namespace) -> None:
    config_path = _native_config_path(arguments, required=True)
    assert config_path is not None
    session = prepare_native_session(
        pid=arguments.pid,
        pipe_name=arguments.pipe,
        config=load_native_config(config_path),
    )
    # Session material includes pipe/nonces/challenges and is intentionally
    # never written through the generic public-artifact writer.
    write_private_native_session_descriptor(arguments.session_out, session)
    _emit({"status": "ok", "command": "native-session prepare"})


def _native_doctor(arguments: argparse.Namespace) -> None:
    _emit(native_doctor_status(_native_config_path(arguments, required=False)))


def _native_audit(arguments: argparse.Namespace) -> None:
    config_path = _native_config_path(arguments, required=True)
    assert config_path is not None
    targets = _bound_new_output_paths(
        arguments.audit_out,
        arguments.report_out,
        forbidden=(arguments.input, arguments.session, config_path),
    )
    try:
        with consume_native_session(arguments.session) as session:
            with bound_native_audit(
                arguments.input,
                session,
                load_native_config(config_path),
            ) as audit:
                publish_artifacts(
                    (
                        ArtifactPublication(
                            path=targets.targets[0].destination,
                            payload=canonical_json_bytes(audit) + b"\n",
                            private=True,
                        ),
                        ArtifactPublication(
                            path=targets.targets[1].destination,
                            payload=render_native_audit_report(audit).encode("utf-8"),
                            private=False,
                        ),
                    ),
                    existing_parents=tuple(
                        target.parent for target in targets.targets
                    ),
                )
    finally:
        targets.close()
    _emit({"status": "ok", "command": "native-audit"})


def _native_plan(arguments: argparse.Namespace) -> None:
    config_path = _native_config_path(arguments, required=True)
    assert config_path is not None
    targets = _bound_new_output_paths(
        arguments.plan_out,
        arguments.review_out,
        forbidden=(arguments.audit, arguments.intent, config_path),
    )
    try:
        audit = load_native_artifact("audit", arguments.audit)
        intent = load_native_artifact("intent", arguments.intent)
        plan = generate_native_plan(
            audit,
            intent,
            load_native_config(config_path),
        )
        publish_artifacts(
            (
                ArtifactPublication(
                    path=targets.targets[0].destination,
                    payload=canonical_json_bytes(plan) + b"\n",
                    private=True,
                ),
                ArtifactPublication(
                    path=targets.targets[1].destination,
                    payload=render_native_plan_review(plan).encode("utf-8"),
                    private=False,
                ),
            ),
            existing_parents=tuple(target.parent for target in targets.targets),
        )
    finally:
        targets.close()
    _emit({"status": "ok", "command": "native-plan"})


def _native_review_plan(arguments: argparse.Namespace) -> None:
    audit = load_native_artifact("audit", arguments.audit)
    intent = load_native_artifact("intent", arguments.intent)
    plan = load_native_artifact("plan", arguments.plan)
    validate_native_plan_against_audit(
        audit,
        intent,
        plan,
        _native_config(arguments),
    )
    print(render_native_plan_review(plan))


def _native_apply(arguments: argparse.Namespace) -> None:
    config = _native_config(arguments)
    audit = load_native_artifact("audit", arguments.audit)
    intent = load_native_artifact("intent", arguments.intent)
    plan = load_native_artifact("plan", arguments.plan)
    with consume_native_session(arguments.session) as session:
        apply_native_dwg(
            arguments.input,
            session,
            audit,
            plan,
            intent,
            config,
            confirm_plan=arguments.confirm_plan,
            output_path=arguments.output,
            verification_path=arguments.verification_out,
        )
    _emit({"status": "ok", "command": "native-apply"})


def _native_verify(arguments: argparse.Namespace) -> None:
    verification = load_native_artifact("verification", arguments.verification)
    verify_native_published_output(arguments.input, verification)
    _emit({"status": "ok", "command": "native-verify"})


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

    native_session = subcommands.add_parser(
        "native-session",
        help="prepare one explicit read-only native bridge session",
        description=(
            "Bind only an explicitly selected PID and local bridge pipe. "
            "The resulting private descriptor is one-use and read-only."
        ),
    )
    native_session_commands = native_session.add_subparsers(
        dest="native_session_command",
        required=True,
    )
    native_prepare = native_session_commands.add_parser(
        "prepare",
        help="bind an explicitly advertised PID and local named pipe",
    )
    native_prepare.add_argument("--pid", type=int, required=True)
    native_prepare.add_argument("--pipe", required=True)
    native_prepare.add_argument("--session-out", type=Path, required=True)
    native_prepare.add_argument("--native-config", type=Path)
    native_prepare.set_defaults(handler=_native_session_prepare)

    native_doctor = subcommands.add_parser(
        "native-doctor",
        help="report optional external native-adapter readiness without drawing access",
    )
    native_doctor.add_argument("--native-config", type=Path)
    native_doctor.set_defaults(handler=_native_doctor)

    native_audit = subcommands.add_parser(
        "native-audit",
        help="create a redacted read-only native audit from an explicit session",
    )
    native_audit.add_argument("--input", type=Path, required=True)
    native_audit.add_argument("--session", type=Path, required=True)
    native_audit.add_argument("--audit-out", type=Path, required=True)
    native_audit.add_argument("--report-out", type=Path, required=True)
    native_audit.add_argument("--native-config", type=Path)
    native_audit.set_defaults(handler=_native_audit)

    native_plan = subcommands.add_parser(
        "native-plan",
        help="generate a deterministic redacted native plan from private intent",
    )
    native_plan.add_argument("--audit", type=Path, required=True)
    native_plan.add_argument("--intent", type=Path, required=True)
    native_plan.add_argument("--plan-out", type=Path, required=True)
    native_plan.add_argument("--review-out", type=Path, required=True)
    native_plan.add_argument("--native-config", type=Path)
    native_plan.set_defaults(handler=_native_plan)

    native_review = subcommands.add_parser(
        "native-review-plan",
        help="validate a native plan against its audit and private intent",
    )
    native_review.add_argument("--audit", type=Path, required=True)
    native_review.add_argument("--intent", type=Path, required=True)
    native_review.add_argument("--plan", type=Path, required=True)
    native_review.add_argument("--native-config", type=Path)
    native_review.set_defaults(handler=_native_review_plan)

    native_apply = subcommands.add_parser(
        "native-apply",
        help="copy-only fixed-command native apply with mandatory readback",
    )
    native_apply.add_argument("--input", type=Path, required=True)
    native_apply.add_argument("--session", type=Path, required=True)
    native_apply.add_argument("--audit", type=Path, required=True)
    native_apply.add_argument("--intent", type=Path, required=True)
    native_apply.add_argument("--plan", type=Path, required=True)
    native_apply.add_argument("--confirm-plan", required=True)
    native_apply.add_argument("--output", type=Path, required=True)
    native_apply.add_argument("--verification-out", type=Path, required=True)
    native_apply.add_argument("--native-config", type=Path)
    native_apply.set_defaults(handler=_native_apply)

    native_verify = subcommands.add_parser(
        "native-verify",
        help="recheck that native verification evidence still binds an output",
    )
    native_verify.add_argument("--input", type=Path, required=True)
    native_verify.add_argument("--verification", type=Path, required=True)
    native_verify.set_defaults(handler=_native_verify)
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
    except (CanonicalJsonError, RecursionError):
        # Every normal JSON boundary maps earlier to a domain error. Keep this
        # final CLI guard fail-closed too, so a future boundary cannot expose
        # a Python recursion traceback or mislabel hostile JSON as internal.
        _emit({"status": "error", "code": ErrorCode.INVALID_ARGUMENT.value}, stream=sys.stderr)
        return 1
    except (OSError, OwnershipError, ValueError):
        _emit({"status": "error", "code": ErrorCode.INTERNAL_ERROR.value}, stream=sys.stderr)
        return 1
    return 0
