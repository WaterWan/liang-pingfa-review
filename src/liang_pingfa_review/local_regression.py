"""Explicit opt-in local-only regression runner for authorized DWG inputs."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .apply import ApplyResult, Converter, apply_dwg
from .audit import audit_dwg
from .canonical import describe_source
from .errors import ErrorCode, PipelineError
from .plan import generate_edit_plan
from .temporary import PrivateWorkspace
from .verify import verify_dwg


def run_local_regression(
    *,
    source_environment_variable: str,
    work_root: Path,
    converter: Converter,
) -> dict[str, Any]:
    """Run a disposable full workflow from an explicitly supplied environment key."""

    source_value = os.environ.get(source_environment_variable)
    if not source_value:
        raise PipelineError(
            ErrorCode.LOCAL_REGRESSION_SOURCE_MISSING,
            "local source environment variable is unset",
        )
    if not work_root.is_dir():
        raise PipelineError(ErrorCode.INVALID_ARGUMENT, "local regression work root is missing")
    source = Path(source_value)
    before = describe_source(source)
    with PrivateWorkspace(
        prefix="liang-pingfa-local-regression-", directory=work_root
    ) as root:
        audit = audit_dwg(source, converter)
        plan = generate_edit_plan(audit)
        output = root / "corrected.dwg"
        apply_result = apply_dwg(
            source,
            audit,
            plan,
            plan["plan_id"],
            output,
            converter,
        )
        if (
            not isinstance(apply_result, ApplyResult)
            or apply_result.published_output_binding is None
        ):
            raise PipelineError(
                ErrorCode.INTERNAL_ERROR,
                "local regression apply has no published output binding",
            )
        # Transfer only the identity computed while apply still held the
        # publication handle. Reopening the current pathname without this
        # binding could adopt and later delete another writer's replacement.
        root.track_created_file(
            output,
            expected_binding=apply_result.published_output_binding,
        )
        verification = verify_dwg(output, audit, plan, converter)
        if describe_source(source) != before:
            raise PipelineError(
                ErrorCode.SOURCE_CHANGED_DURING_RUN,
                "local regression changed its source",
            )
        return verification
