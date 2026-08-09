#!/usr/bin/env python3
"""Regression fixtures for the xctrace Activity Monitor XML summary CLI."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from xml.sax.saxutils import escape


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "misc/ios/activity_trace_summary.py"
COLUMNS = (
    "start",
    "process",
    "pid",
    "cpu-percent",
    "memory-physical-footprint",
)
MIB = 1024 * 1024


def activity_xml(
    rows: list[dict[str, object]],
    *,
    columns: tuple[str, ...] = COLUMNS,
    schema_name: str = "activity-monitor-process-live",
    shared_refs: bool = False,
) -> str:
    """Return the relevant xctrace table shape, optionally using shared refs."""

    shared: list[str] = []
    rendered_rows: list[str] = []
    for row_index, row in enumerate(rows):
        cells: list[str] = []
        defaults = {
            "start": str(float(row.get("seconds", 0.0)) * 1_000_000_000.0),
            "process": str(row.get("process", f"xr_3da ({row.get('pid', 42)})")),
            "pid": str(row.get("pid", 42)),
            "cpu-percent": row.get("cpu", "1"),
            "memory-physical-footprint": str(float(row.get("footprint_mib", 100.0)) * MIB),
        }
        for column_index, column in enumerate(columns):
            value = row.get(column, defaults.get(column, "unused"))
            identifier = f"r{row_index}c{column_index}"
            if value is None:
                definition = f'<sentinel id="{identifier}"/>'
            else:
                attrs = f' id="{identifier}"'
                if column == "process":
                    attrs += f' fmt="{escape(str(value), {"\"": "&quot;"})}"'
                definition = f"<value{attrs}>{escape(str(value))}</value>"
            if shared_refs:
                shared.append(definition)
                cells.append(f'<value ref="{identifier}"/>')
            else:
                cells.append(definition.replace(f' id="{identifier}"', "", 1))
        rendered_rows.append("<row>" + "".join(cells) + "</row>")

    schema = "".join(f"<col><mnemonic>{column}</mnemonic></col>" for column in columns)
    shared_section = "<shared>" + "".join(shared) + "</shared>" if shared else ""
    return (
        "<trace-query-result>"
        f"<node><schema name=\"{schema_name}\">{schema}</schema>"
        f"{''.join(rendered_rows)}</node>{shared_section}"
        "</trace-query-result>"
    )


class ActivityTraceSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.temp = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def run_cli(self, xml: str, *arguments: str) -> subprocess.CompletedProcess[str]:
        source = self.temp / "activity.xml"
        source.write_text(xml, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(source), *arguments],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def assert_exit(
        self, expected: int, xml: str, *arguments: str
    ) -> subprocess.CompletedProcess[str]:
        result = self.run_cli(xml, *arguments)
        self.assertEqual(expected, result.returncode, result.stderr + result.stdout)
        return result

    def stable_rows(self) -> list[dict[str, object]]:
        return [
            {"seconds": 0, "pid": 42, "cpu": 1, "footprint_mib": 100},
            {"seconds": 2, "pid": 42, "cpu": 2, "footprint_mib": 110},
            {"seconds": 4, "pid": 42, "cpu": 3, "footprint_mib": 120},
        ]

    def test_stable_shared_refs_summary_and_json(self) -> None:
        output = self.temp / "summary.json"
        result = self.assert_exit(
            0,
            activity_xml(self.stable_rows(), shared_refs=True),
            "--json-output",
            str(output),
        )
        expected = {
            "cpu_percent": {"min": 1.0, "mean": 2.0, "p90": 3.0, "max": 3.0},
            "duration_seconds": 4.0,
            "failures": [],
            "footprint_mib": {
                "first": 100.0,
                "last": 120.0,
                "min": 100.0,
                "mean": 110.0,
                "p90": 120.0,
                "max": 120.0,
                "growth": 20.0,
            },
            "max_gap_seconds": 2.0,
            "pids": [42],
            "process": "xr_3da",
            "result": "PASS",
            "samples": 3,
        }
        self.assertEqual(expected, json.loads(result.stdout))
        self.assertEqual(expected, json.loads(output.read_text(encoding="utf-8")))
        self.assertTrue(output.read_text(encoding="utf-8").endswith("\n"))

    def test_unordered_rows_are_sorted(self) -> None:
        rows = list(reversed(self.stable_rows()))
        result = self.assert_exit(0, activity_xml(rows))
        summary = json.loads(result.stdout)
        self.assertEqual(100.0, summary["footprint_mib"]["first"])
        self.assertEqual(120.0, summary["footprint_mib"]["last"])
        self.assertEqual(4.0, summary["duration_seconds"])

    def test_warmup_partial(self) -> None:
        result = self.assert_exit(
            0, activity_xml(self.stable_rows()), "--warmup-seconds", "2"
        )
        summary = json.loads(result.stdout)
        self.assertEqual(2, summary["samples"])
        self.assertEqual(2.0, summary["duration_seconds"])

    def test_warmup_removes_every_sample(self) -> None:
        self.assert_exit(
            1, activity_xml(self.stable_rows()), "--warmup-seconds", "5"
        )

    def test_no_matching_process_is_unmet(self) -> None:
        self.assert_exit(
            1,
            activity_xml([{"seconds": 0, "process": "Other (42)"}]),
            "--process",
            "xr_3da",
        )

    def test_bad_schema_and_missing_columns_are_malformed(self) -> None:
        self.assert_exit(2, activity_xml(self.stable_rows(), schema_name="other"))
        self.assert_exit(
            2,
            activity_xml(
                self.stable_rows(),
                columns=("start", "process", "pid", "cpu-percent"),
            ),
        )
        self.assert_exit(
            2,
            activity_xml(
                self.stable_rows(),
                columns=COLUMNS + ("pid",),
            ),
        )

    def test_row_mismatch_is_malformed(self) -> None:
        xml = activity_xml(self.stable_rows()).replace(
            "</row>", "<value>extra</value></row>", 1
        )
        self.assert_exit(2, xml)

    def test_invalid_dangling_and_cyclic_refs_are_malformed(self) -> None:
        dangling = activity_xml(self.stable_rows(), shared_refs=True).replace(
            'ref="r0c0"', 'ref="missing"', 1
        )
        cyclic = activity_xml(self.stable_rows(), shared_refs=True).replace(
            "<shared>", '<shared><value id="cycle" ref="r0c0"/>', 1
        ).replace(
            '<value id="r0c0">0.0</value>', '<value id="r0c0" ref="cycle"/>', 1
        )
        invalid = activity_xml(self.stable_rows(), shared_refs=True).replace(
            'ref="r0c0"', 'ref=""', 1
        )
        for xml in (dangling, cyclic, invalid):
            with self.subTest(xml=xml[:80]):
                self.assert_exit(2, xml)

    def test_duplicate_xml_id_is_malformed(self) -> None:
        xml = activity_xml(self.stable_rows(), shared_refs=True).replace(
            'id="r0c1"', 'id="r0c0"', 1
        )
        self.assert_exit(2, xml)

    def test_multiple_pid_requires_continuity(self) -> None:
        rows = self.stable_rows()
        rows[-1]["pid"] = 43
        result = self.assert_exit(
            1, activity_xml(rows), "--require-single-pid"
        )
        self.assertEqual([42, 43], json.loads(result.stdout)["pids"])

    def test_minimum_samples_and_duration_are_unmet(self) -> None:
        self.assert_exit(1, activity_xml(self.stable_rows()), "--min-samples", "4")
        self.assert_exit(
            1, activity_xml(self.stable_rows()), "--min-duration-seconds", "5"
        )

    def test_max_gap_and_compatibility_alias_are_unmet(self) -> None:
        rows = self.stable_rows()
        rows[1]["seconds"] = 4
        rows[2]["seconds"] = 8
        xml = activity_xml(rows)
        self.assert_exit(1, xml, "--max-gap-seconds", "3")
        self.assert_exit(1, xml, "--max-sample-gap-seconds", "3")

    def test_footprint_and_growth_limits_are_unmet(self) -> None:
        xml = activity_xml(self.stable_rows())
        self.assert_exit(1, xml, "--max-footprint-mib", "119")
        self.assert_exit(1, xml, "--max-growth-mib", "19")

    def test_nonfinite_timestamp_pid_and_footprint_are_malformed(self) -> None:
        for column in ("start", "pid", "memory-physical-footprint"):
            for value in ("NaN", "Infinity"):
                rows = self.stable_rows()
                rows[0][column] = value
                with self.subTest(column=column, value=value):
                    self.assert_exit(2, activity_xml(rows))

    def test_negative_timestamp_and_footprint_are_malformed(self) -> None:
        timestamp = self.stable_rows()
        timestamp[0]["start"] = "-1"
        footprint = self.stable_rows()
        footprint[0]["memory-physical-footprint"] = "-1"
        self.assert_exit(2, activity_xml(timestamp))
        self.assert_exit(2, activity_xml(footprint))

    def test_fractional_zero_and_negative_pid_are_malformed(self) -> None:
        for value in ("42.5", "0", "-1"):
            rows = self.stable_rows()
            rows[0]["pid"] = value
            with self.subTest(pid=value):
                self.assert_exit(2, activity_xml(rows))

    def test_cpu_sentinel_and_nonfinite_values_are_ignored(self) -> None:
        rows = self.stable_rows()
        rows[0]["cpu"] = None
        rows[1]["cpu"] = "NaN"
        rows[2]["cpu"] = "Infinity"
        result = self.assert_exit(0, activity_xml(rows))
        self.assertNotIn("cpu_percent", json.loads(result.stdout))

    def test_json_output_write_failure_is_an_input_output_error(self) -> None:
        missing_parent = self.temp / "missing" / "summary.json"
        result = self.assert_exit(
            2,
            activity_xml(self.stable_rows()),
            "--json-output",
            str(missing_parent),
        )
        self.assertIn("could not write JSON summary", result.stderr)

    def test_invalid_required_number_is_malformed(self) -> None:
        rows = self.stable_rows()
        rows[0]["memory-physical-footprint"] = "not-a-number"
        self.assert_exit(2, activity_xml(rows))

    def test_required_sentinel_yields_no_samples(self) -> None:
        rows = self.stable_rows()
        for row in rows:
            row["memory-physical-footprint"] = None
        self.assert_exit(1, activity_xml(rows))


if __name__ == "__main__":
    unittest.main()
