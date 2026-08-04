"""Explicit native command-family boundary tests."""

from __future__ import annotations

from contextlib import contextmanager
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from liang_pingfa_review import cli
from liang_pingfa_review.cli import build_parser, main
from tests.support.synthetic_native import config, session


class NativeCliTests(unittest.TestCase):
    """Ensure no generic backend switch or hidden fallback enters the CLI."""

    def test_native_commands_are_explicit_and_legacy_commands_remain(self) -> None:
        parser = build_parser()
        actions = next(
            action
            for action in parser._actions
            if action.dest == "command"
        )
        commands = set(actions.choices)
        self.assertTrue(
            {
                "doctor",
                "audit",
                "plan",
                "review-plan",
                "apply",
                "verify",
                "local-regression",
                "native-session",
                "native-doctor",
                "native-audit",
                "native-plan",
                "native-review-plan",
                "native-apply",
                "native-verify",
            }.issubset(commands)
        )
        self.assertNotIn("backend", parser.format_help().casefold())

    def test_native_doctor_requires_no_drawing_and_is_machine_redacted(self) -> None:
        with mock.patch("liang_pingfa_review.cli._emit") as emitted:
            self.assertEqual(main(["native-doctor"]), 0)
        event = emitted.call_args.args[0]
        self.assertEqual(event["command"], "native-doctor")
        self.assertNotIn("\\\\", " ".join(event.values()))

    def test_native_verify_fails_closed_away_from_windows(self) -> None:
        if os.name == "nt":
            self.skipTest("Windows has a separate ownership precondition path")
        error = StringIO()
        with redirect_stderr(error):
            self.assertEqual(
                main(
                    [
                        "native-verify",
                        "--input",
                        "missing.dwg",
                        "--verification",
                        "missing.json",
                    ]
                ),
                1,
            )
        self.assertIn("NATIVE_VERIFICATION_INVALID", error.getvalue())

    def test_native_session_prepare_uses_private_descriptor_writer_not_public_artifacts(self) -> None:
        arguments = SimpleNamespace(
            native_config=Path("generated-config.json"),
            pid=1234,
            pipe=chr(92) * 2
            + "."
            + chr(92)
            + "pipe"
            + chr(92)
            + "liang-pingfa-native-a1b2c3d4e5f6g7h8",
            session_out=Path("generated-private-session.json"),
        )
        descriptor = session()
        with (
            mock.patch.object(cli, "prepare_native_session", return_value=descriptor) as prepare,
            mock.patch.object(cli, "load_native_config", return_value=config()),
            mock.patch.object(cli, "write_private_native_session_descriptor") as writer,
            mock.patch.object(
                cli,
                "write_new_artifacts",
                side_effect=AssertionError("public artifact writer must not receive session"),
            ),
            mock.patch.object(cli, "_emit") as emit,
        ):
            cli._native_session_prepare(arguments)
        prepare.assert_called_once()
        writer.assert_called_once_with(arguments.session_out, descriptor)
        self.assertEqual(emit.call_args.args[0], {"status": "ok", "command": "native-session prepare"})

    def test_native_audit_and_plan_mark_only_machine_json_private(self) -> None:
        """CLI routes native pairs to the mixed retained-handle publisher."""

        root = Path("generated-native-cli")
        parent = object()
        targets = SimpleNamespace(
            targets=(
                SimpleNamespace(destination=root / "audit.json", parent=parent),
                SimpleNamespace(destination=root / "audit.md", parent=parent),
            ),
            close=mock.Mock(),
        )
        arguments = SimpleNamespace(
            native_config=Path("generated-config.json"),
            input=Path("generated-source.dwg"),
            session=Path("generated-session.json"),
            audit_out=root / "audit.json",
            report_out=root / "audit.md",
        )

        @contextmanager
        def consumed_session(_path: Path):
            yield session()

        @contextmanager
        def audited(*_args: object, **_kwargs: object):
            yield {"generated": "audit"}

        with (
            mock.patch.object(cli, "_bound_new_output_paths", return_value=targets),
            mock.patch.object(cli, "_native_config_path", return_value=Path("generated-config.json")),
            mock.patch.object(cli, "consume_native_session", consumed_session),
            mock.patch.object(cli, "bound_native_audit", audited),
            mock.patch.object(cli, "load_native_config", return_value=config()),
            mock.patch.object(cli, "publish_artifacts") as publisher,
            mock.patch.object(cli, "_emit"),
        ):
            cli._native_audit(arguments)
        entries = publisher.call_args.args[0]
        self.assertEqual([entry.private for entry in entries], [True, False])
        self.assertTrue(entries[0].path.name.endswith(".json"))
        self.assertTrue(entries[1].path.name.endswith(".md"))

        plan_targets = SimpleNamespace(
            targets=(
                SimpleNamespace(destination=root / "plan.json", parent=parent),
                SimpleNamespace(destination=root / "plan.md", parent=parent),
            ),
            close=mock.Mock(),
        )
        plan_arguments = SimpleNamespace(
            native_config=Path("generated-config.json"),
            audit=Path("generated-audit.json"),
            intent=Path("generated-intent.json"),
            plan_out=root / "plan.json",
            review_out=root / "plan.md",
        )
        with (
            mock.patch.object(cli, "_bound_new_output_paths", return_value=plan_targets),
            mock.patch.object(cli, "_native_config_path", return_value=Path("generated-config.json")),
            mock.patch.object(cli, "load_native_artifact", side_effect=({"audit": 1}, {"intent": 1})),
            mock.patch.object(cli, "load_native_config", return_value=config()),
            mock.patch.object(cli, "generate_native_plan", return_value={"generated": "plan"}),
            mock.patch.object(cli, "publish_artifacts") as publisher,
            mock.patch.object(cli, "_emit"),
        ):
            cli._native_plan(plan_arguments)
        entries = publisher.call_args.args[0]
        self.assertEqual([entry.private for entry in entries], [True, False])


if __name__ == "__main__":
    unittest.main()
