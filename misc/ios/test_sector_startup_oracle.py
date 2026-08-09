#!/usr/bin/env python3
"""Mutation-backed regression tests for the iOS startup-sector log oracle."""

from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path
from decimal import Decimal
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "misc/ios/sector_startup_oracle.py"
INVALID_SECTOR = 0xFFFFFFFF
MAX_EPOCH = 0xFFFFFFFFFFFFFFFF
POLICY_RADII = (
    Decimal("0.5"),
    Decimal("1.0"),
    Decimal("2.0"),
    Decimal("4.0"),
    Decimal("8.0"),
    Decimal("16.0"),
    Decimal("32.0"),
)
POLICY_DIRECTIONS = (
    (Decimal("1"), Decimal("0")),
    (Decimal("-1"), Decimal("0")),
    (Decimal("0"), Decimal("1")),
    (Decimal("0"), Decimal("-1")),
    (Decimal("0.70710678"), Decimal("0.70710678")),
    (Decimal("-0.70710678"), Decimal("0.70710678")),
    (Decimal("0.70710678"), Decimal("-0.70710678")),
    (Decimal("-0.70710678"), Decimal("-0.70710678")),
)


def coordinate(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.001")), ".3f")


def marker(
    *,
    pid: int = 42,
    epoch: int = 1,
    frame: int = 10,
    level: str = "zaton",
    trigger: str = "level_load",
    status: str = "resolved",
    method: str = "exact",
    sector: int = 7,
    camera: tuple[str, str, str] = ("1.000", "2.000", "3.000"),
    probe: tuple[str, str, str] | None = None,
    radius: str = "0.0",
) -> str:
    actual_probe = camera if probe is None else probe
    return (
        f"* iOS sector startup v1 pid={pid} epoch={epoch} frame={frame} level={level} "
        f"trigger={trigger} status={status} method={method} sector={sector} "
        f"camera=({','.join(camera)}) probe=({','.join(actual_probe)}) radius={radius}"
    )


def fallback_marker(**overrides: object) -> str:
    values: dict[str, object] = {
        "method": "fallback",
        "probe": ("1.500", "2.000", "3.000"),
        "radius": "0.5",
    }
    values.update(overrides)
    return marker(**values)  # type: ignore[arg-type]


def unresolved_marker(**overrides: object) -> str:
    values: dict[str, object] = {
        "status": "unresolved",
        "method": "fallback",
        "sector": INVALID_SECTOR,
    }
    values.update(overrides)
    return marker(**values)  # type: ignore[arg-type]


class SectorStartupOracleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        specification = importlib.util.spec_from_file_location("sector_startup_oracle", SCRIPT)
        if specification is None or specification.loader is None:
            raise RuntimeError("could not load sector_startup_oracle module")
        module = importlib.util.module_from_spec(specification)
        sys.modules[specification.name] = module
        specification.loader.exec_module(module)
        cls.oracle = module

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.temp = Path(self.temporary_directory.name).resolve()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def run_cli(self, lines: list[str], *arguments: str) -> subprocess.CompletedProcess[str]:
        source = self.temp / "openxray.log"
        source.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return self.run_cli_path(source, *arguments)

    def run_cli_path(self, source: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        actual_arguments = list(arguments)
        if "--expected-pid" not in actual_arguments:
            actual_arguments.extend(("--expected-pid", "42"))
        if "--after-epoch" not in actual_arguments:
            actual_arguments.extend(("--after-epoch", "0"))
        if "--expect-trigger" not in actual_arguments:
            actual_arguments.extend(("--expect-trigger", "level_load"))
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(source), *actual_arguments],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def assert_pass(self, lines: list[str], *arguments: str) -> dict[str, object]:
        result = self.run_cli(lines, *arguments)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        return json.loads(result.stdout)

    def assert_fail(self, lines: list[str], expected: str, *arguments: str) -> None:
        result = self.run_cli(lines, *arguments)
        self.assertEqual(2, result.returncode, result.stderr + result.stdout)
        self.assertIn(expected, result.stderr)
        self.assertEqual("", result.stdout)

    def test_all_terminal_classifications(self) -> None:
        cases = {
            "exact": ([marker()], "level_load"),
            "fallback": ([fallback_marker()], "level_load"),
            "retained": ([marker(trigger="quick_load", method="retained")], "quick_load"),
            "unresolved-fallback": ([unresolved_marker()], "level_load"),
            "unresolved-none": (
                [unresolved_marker(trigger="quick_load", method="none")],
                "quick_load",
            ),
        }
        for name, (lines, expected_trigger) in cases.items():
            with self.subTest(name=name):
                report = self.assert_pass(
                    lines, "--expect-trigger", expected_trigger
                )
                epoch = report["epochs"][0]
                expected_classification = "unresolved" if name.startswith("unresolved") else name
                self.assertEqual(expected_classification, epoch["classification"])
                if expected_classification == "unresolved":
                    self.assertEqual(name.split("-", 1)[1], epoch["unresolved_method"])

    def test_recovery_classifies_both_unresolved_sources(self) -> None:
        cases = (
            (
                unresolved_marker(frame=10, method="fallback"),
                marker(frame=11, method="exact"),
                "fallback",
                "exact",
            ),
            (
                unresolved_marker(frame=20, trigger="quick_load", method="none"),
                fallback_marker(frame=21, trigger="quick_load"),
                "none",
                "fallback",
            ),
        )
        for first, second, unresolved_method, recovery_method in cases:
            with self.subTest(unresolved_method=unresolved_method):
                expected_trigger = "quick_load" if "trigger=quick_load" in first else "level_load"
                report = self.assert_pass(
                    [first, second], "--expect-trigger", expected_trigger
                )
                epoch = report["epochs"][0]
                self.assertEqual("recovered", epoch["classification"])
                self.assertEqual(unresolved_method, epoch["unresolved_method"])
                self.assertEqual(recovery_method, epoch["recovery_method"])

    def test_multiple_monotonic_epochs_and_deterministic_json(self) -> None:
        lines = [
            "unrelated engine line",
            marker(epoch=4, frame=100),
            marker(epoch=5, frame=101, trigger="quick_load", method="retained"),
        ]
        expected = (
            "--after-epoch", "3",
            "--expect-trigger", "level_load",
            "--expect-trigger", "quick_load",
        )
        report = self.assert_pass(lines, *expected)
        self.assertEqual("PASS", report["result"])
        self.assertEqual(2, report["markers"])
        self.assertEqual([4, 5], [epoch["epoch"] for epoch in report["epochs"]])
        self.assertEqual(
            {
                "after_epoch": 3,
                "epochs": [4, 5],
                "triggers": ["level_load", "quick_load"],
            },
            report["expectation"],
        )

        second = self.run_cli(lines, *expected)
        self.assertEqual(json.dumps(report, indent=2, sort_keys=True) + "\n", second.stdout)

    def test_epoch_gap_fails_closed(self) -> None:
        self.assert_fail(
            [marker(epoch=4, frame=100), marker(epoch=8, frame=101)],
            "epoch gap after 4: expected 5, got 8",
        )

    def test_anchored_expectation_rejects_missing_final_extra_and_wrong_trigger(self) -> None:
        batch = (
            "--after-epoch", "3",
            "--expect-trigger", "level_load",
            "--expect-trigger", "quick_load",
        )
        self.assert_fail(
            [marker(epoch=4, frame=100)],
            "expected anchored epochs [4, 5], got [4]",
            *batch,
        )
        self.assert_fail(
            [
                marker(epoch=4, frame=100),
                marker(epoch=5, frame=101, trigger="quick_load", method="retained"),
                marker(epoch=6, frame=102, trigger="quick_load", method="retained"),
            ],
            "expected anchored epochs [4, 5], got [4, 5, 6]",
            *batch,
        )
        self.assert_fail(
            [marker(epoch=4, frame=100), marker(epoch=5, frame=101)],
            "expected trigger sequence ['level_load', 'quick_load']",
            *batch,
        )
        self.assert_fail(
            [marker(epoch=MAX_EPOCH, frame=100)],
            "expected epoch range exceeds uint64",
            "--after-epoch", str(MAX_EPOCH),
            "--expect-trigger", "level_load",
        )

    def test_cli_requires_pid_anchor_and_expected_trigger(self) -> None:
        source = self.temp / "required.log"
        source.write_text(marker() + "\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(source)],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn("--expected-pid", result.stderr)
        self.assertIn("--after-epoch", result.stderr)
        self.assertIn("--expect-trigger", result.stderr)

    def test_no_marker_and_malformed_prefixed_line_fail_closed(self) -> None:
        self.assert_fail(["ordinary log line"], "no iOS sector startup v1 markers")
        malformed_candidates = (
            marker().replace("startup v1", "startupv1", 1),
            marker().replace("startup v1", "startup v2", 1),
            marker().replace("v1 pid=", "v1pid=", 1),
            marker().replace("v1 pid=", "v1\tpid=", 1),
            marker().replace("v1 pid=", "v1  pid=", 1),
            "* iOS sector startup v1",
            marker() + " extra=field",
            marker() + "x",
            marker().replace("method=exact", "method=unknown"),
        )
        for malformed in malformed_candidates:
            with self.subTest(malformed=malformed):
                self.assert_fail([marker(), malformed], "malformed v1 marker")

    def test_noncanonical_and_nonfinite_numbers_fail(self) -> None:
        canonical = marker()
        mutations = (
            canonical.replace("1.000", "1.0", 1),
            canonical.replace("1.000", "01.000", 1),
            canonical.replace("1.000", "nan", 1),
            canonical.replace("radius=0.0", "radius=-0.0", 1),
        )
        for mutated in mutations:
            with self.subTest(mutated=mutated):
                self.assert_fail([mutated], "malformed v1 marker")

    def test_pid_change_and_expected_pid_mismatch_fail(self) -> None:
        self.assert_fail(
            [marker(frame=10, epoch=1), marker(pid=43, frame=11, epoch=2)],
            "marker PID changed",
        )
        self.assert_fail([marker()], "does not match expected PID", "--expected-pid", "41")

    def test_frame_duplicates_and_regressions_fail(self) -> None:
        for second_frame in (10, 9):
            with self.subTest(second_frame=second_frame):
                self.assert_fail(
                    [marker(frame=10, epoch=1), marker(frame=second_frame, epoch=2)],
                    "frame order is not strictly increasing",
                )

    def test_epoch_return_and_duplicate_terminal_outcome_fail(self) -> None:
        self.assert_fail(
            [marker(epoch=1, frame=10), marker(epoch=2, frame=11), marker(epoch=1, frame=12)],
            "epoch returned",
        )
        self.assert_fail(
            [marker(epoch=1, frame=10), marker(epoch=1, frame=11)],
            "only unresolved -> detected resolved is permitted",
        )

    def test_level_and_trigger_mismatch_within_epoch_fail(self) -> None:
        first = unresolved_marker(frame=10)
        self.assert_fail([first, marker(frame=11, level="jupiter")], "level changed within the epoch")
        self.assert_fail(
            [first, marker(frame=11, trigger="quick_load")],
            "trigger changed within the epoch",
        )

    def test_retained_contract_and_recovery_restrictions(self) -> None:
        self.assert_fail(
            [marker(method="retained", trigger="level_load")],
            "retained sector is valid only for quick_load",
        )
        self.assert_fail(
            [
                unresolved_marker(frame=10, trigger="quick_load", method="none"),
                marker(frame=11, trigger="quick_load", method="retained"),
            ],
            "only unresolved -> detected resolved is permitted",
        )

    def test_status_method_sector_and_radius_contradictions_fail(self) -> None:
        mutations = (
            (marker(sector=INVALID_SECTOR), "resolved marker has the invalid sector"),
            (marker(method="none"), "resolved marker cannot use method=none"),
            (unresolved_marker(sector=7), "unresolved marker must use the invalid sector"),
            (unresolved_marker(method="exact"), "unresolved marker cannot use method=exact"),
            (fallback_marker(radius="3.0"), "fallback radius is not in the policy"),
            (
                fallback_marker(probe=("1.500", "2.002", "3.000")),
                "fallback probe does not match a policy direction",
            ),
            (
                fallback_marker(probe=("1.000", "2.000", "3.000")),
                "fallback probe does not match a policy direction",
            ),
            (
                fallback_marker(probe=("1.300", "2.000", "3.400")),
                "fallback probe does not match a policy direction",
            ),
            (marker(method="exact", probe=("1.001", "2.000", "3.000")), "exact marker metadata"),
            (unresolved_marker(probe=("1.001", "2.000", "3.000")), "unresolved marker metadata"),
        )
        for mutated, expected in mutations:
            with self.subTest(expected=expected):
                self.assert_fail([mutated], expected)

        raw_camera = (Decimal("-10.12349"), Decimal("-2.34549"), Decimal("7.89149"))
        camera_text = tuple(coordinate(component) for component in raw_camera)
        for radius in POLICY_RADII:
            for direction_x, direction_z in POLICY_DIRECTIONS:
                raw_probe = (
                    raw_camera[0] + direction_x * radius,
                    raw_camera[1],
                    raw_camera[2] + direction_z * radius,
                )
                probe_text = tuple(coordinate(component) for component in raw_probe)
                with self.subTest(radius=radius, direction=(direction_x, direction_z)):
                    self.assert_pass(
                        [
                            fallback_marker(
                                camera=camera_text,
                                probe=probe_text,
                                radius=format(radius, ".1f"),
                            )
                        ]
                    )

    def test_integer_width_and_leading_zero_contracts(self) -> None:
        self.assert_fail(
            [marker().replace("epoch=1", "epoch=18446744073709551616")],
            "epoch exceeds uint64",
        )
        self.assert_fail(
            [marker().replace("frame=10", "frame=4294967296")],
            "frame exceeds uint32",
        )
        self.assert_fail(
            [marker().replace("sector=7", "sector=4294967296")],
            "sector exceeds uint32",
        )
        self.assert_fail([marker().replace("epoch=1", "epoch=01")], "malformed v1 marker")

    def test_json_output_is_new_regular_path_and_matches_stdout(self) -> None:
        output = self.temp / "report.json"
        result = self.run_cli([marker()], "--json-output", str(output))
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertEqual(result.stdout, output.read_text(encoding="utf-8"))
        self.assertEqual(0o600, stat.S_IMODE(output.stat().st_mode))

        self.assert_fail([marker()], "already exists", "--json-output", str(output))
        self.assertEqual(result.stdout, output.read_text(encoding="utf-8"))

        replace_parent = self.temp / "replace-parent"
        replace_parent.mkdir()
        held_parent = self.temp / "held-parent"
        attacker_parent = self.temp / "attacker-parent"
        attacker_parent.mkdir()
        replacement_output = replace_parent / "report.json"

        def replace_after_open() -> None:
            replace_parent.rename(held_parent)
            replace_parent.symlink_to(attacker_parent, target_is_directory=True)

        self.oracle.write_new_report(
            replacement_output,
            '{"result":"PASS"}',
            after_parent_open=replace_after_open,
        )
        self.assertEqual(
            '{"result":"PASS"}\n',
            (held_parent / "report.json").read_text(encoding="utf-8"),
        )
        self.assertFalse((attacker_parent / "report.json").exists())

        partial_output = self.temp / "partial.json"
        original_write = self.oracle.os.write
        write_calls = 0

        def fail_after_one_byte(file_descriptor: int, data: bytes) -> int:
            nonlocal write_calls
            write_calls += 1
            if write_calls == 1:
                return original_write(file_descriptor, data[:1])
            raise OSError("forced write failure")

        with mock.patch.object(self.oracle.os, "write", side_effect=fail_after_one_byte):
            with self.assertRaisesRegex(self.oracle.OracleError, "could not create JSON output"):
                self.oracle.write_new_report(partial_output, '{"result":"PASS"}')
        self.assertTrue(partial_output.is_file())
        self.assertEqual(b"{", partial_output.read_bytes())

    def test_json_output_rejects_input_and_output_symlink_components(self) -> None:
        source = self.temp / "source.log"
        source.write_text(marker() + "\n", encoding="utf-8")
        source_link = self.temp / "source-link.log"
        source_link.symlink_to(source)
        result = self.run_cli_path(source_link)
        self.assertEqual(2, result.returncode, result.stderr + result.stdout)
        self.assertIn("regular non-symlink log", result.stderr)

        real_input_parent = self.temp / "real-input-parent"
        real_input_parent.mkdir()
        (real_input_parent / "nested").mkdir()
        nested_source = real_input_parent / "nested/openxray.log"
        nested_source.write_text(marker() + "\n", encoding="utf-8")
        input_parent_link = self.temp / "input-parent-link"
        input_parent_link.symlink_to(real_input_parent / "nested", target_is_directory=True)
        result = self.run_cli_path(input_parent_link / "openxray.log")
        self.assertEqual(2, result.returncode, result.stderr + result.stdout)
        self.assertIn("path component is not an accessible non-symlink directory", result.stderr)

        input_ancestor_link = self.temp / "input-ancestor-link"
        input_ancestor_link.symlink_to(real_input_parent, target_is_directory=True)
        result = self.run_cli_path(input_ancestor_link / "nested/openxray.log")
        self.assertEqual(2, result.returncode, result.stderr + result.stdout)
        self.assertIn("path component is not an accessible non-symlink directory", result.stderr)

        existing = self.temp / "existing.json"
        existing.write_text("protected\n", encoding="utf-8")
        target_link = self.temp / "target-link.json"
        target_link.symlink_to(existing)
        self.assert_fail([marker()], "already exists", "--json-output", str(target_link))
        self.assertEqual("protected\n", existing.read_text(encoding="utf-8"))

        real_parent = self.temp / "real-parent"
        real_parent.mkdir()
        parent_link = self.temp / "parent-link"
        parent_link.symlink_to(real_parent, target_is_directory=True)
        self.assert_fail(
            [marker()],
            "path component is not an accessible non-symlink directory",
            "--json-output",
            str(parent_link / "report.json"),
        )
        self.assertFalse((real_parent / "report.json").exists())

        (real_parent / "nested").mkdir()
        ancestor_link = self.temp / "ancestor-link"
        ancestor_link.symlink_to(real_parent, target_is_directory=True)
        self.assert_fail(
            [marker()],
            "path component is not an accessible non-symlink directory",
            "--json-output",
            str(ancestor_link / "nested/report.json"),
        )
        self.assertFalse((real_parent / "nested/report.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
