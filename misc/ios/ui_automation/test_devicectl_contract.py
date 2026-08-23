#!/usr/bin/env python3
"""Deterministic host tests for devicectl JSON and boot-log parsing."""

from __future__ import annotations

import os as _test_feedback_os
if _test_feedback_os.environ.get("XRAY_FEEDBACK_RAW_EVENT_FD"):
    try:
        import sys as _test_feedback_sys
        _test_feedback_sys.path.insert(
            0, _test_feedback_os.path.dirname(_test_feedback_os.path.dirname(__file__)))
        import test_feedback_unittest as _test_feedback_unittest
        _test_feedback_unittest.install_from_environment(
            "python::misc/ios/ui_automation/test_devicectl_contract.py",
            _test_feedback_sys.argv,
        )
    except BaseException:
        pass

import json
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import devicectl_contract as contract  # noqa: E402


PID = 4242
EXECUTABLE = "file:///private/var/containers/Bundle/Application/fixture/xr_3da.app/xr_3da"


def document(result: dict[object, object], outcome: str = "success") -> dict[str, object]:
    return {"info": {"outcome": outcome}, "result": result}


def sector_marker(pid: int = PID, status: str = "resolved") -> bytes:
    return (
        f"* iOS sector startup v1 pid={pid} epoch=1 frame=35 level=zaton "
        f"trigger=level_load status={status} method=exact sector=115 "
        "camera=(1.000,2.000,3.000) probe=(1.000,2.000,3.000) radius=0.0\n"
    ).encode()


class DevicectlContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_json(self, name: str, value: dict[object, object]) -> Path:
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def write_log(self, name: str, content: bytes) -> Path:
        path = self.root / name
        path.write_bytes(content)
        return path

    def test_launch_pid_requires_successful_positive_integer(self) -> None:
        self.assertEqual(contract.launch_pid(document({"process": {"processIdentifier": PID}})), PID)
        for value in (0, -1, True, "4242"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(contract.ContractError, "positive integer"):
                    contract.launch_pid(document({"process": {"processIdentifier": value}}))
        with self.assertRaisesRegex(contract.ContractError, "outcome is not success"):
            contract.launch_pid(document({"process": {"processIdentifier": PID}}, outcome="failure"))

    def test_running_process_requires_one_exact_pid_and_executable(self) -> None:
        response = document({"runningProcesses": [{
            "processIdentifier": PID,
            "executable": EXECUTABLE,
        }]})
        self.assertEqual(contract.verify_running_process(response, expected_pid=PID, executable="xr_3da"), PID)

        wrong_pid = document({"runningProcesses": [{
            "processIdentifier": PID + 1,
            "executable": EXECUTABLE,
        }]})
        with self.assertRaisesRegex(contract.ContractError, "exactly one record"):
            contract.verify_running_process(wrong_pid, expected_pid=PID, executable="xr_3da")

        wrong_executable = document({"runningProcesses": [{
            "processIdentifier": PID,
            "executable": EXECUTABLE[:-6] + "other",
        }]})
        with self.assertRaisesRegex(contract.ContractError, "executable is not"):
            contract.verify_running_process(wrong_executable, expected_pid=PID, executable="xr_3da")

        duplicate_pid = document({"runningProcesses": [
            {"processIdentifier": PID, "executable": EXECUTABLE},
            {"processIdentifier": PID, "executable": EXECUTABLE},
        ]})
        with self.assertRaisesRegex(contract.ContractError, "exactly one record"):
            contract.verify_running_process(duplicate_pid, expected_pid=PID, executable="xr_3da")

        for name, response, expected in (
            ("bound", document({"runningProcesses": [{
                "processIdentifier": PID, "executable": EXECUTABLE,
            }]}), "bound"),
            ("gone", document({"runningProcesses": []}), "gone"),
            ("reused", document({"runningProcesses": [{
                "processIdentifier": PID, "executable": "file:///usr/libexec/other",
            }]}), "reused"),
        ):
            with self.subTest(cleanup_state=name):
                self.assertEqual(
                    contract.cleanup_process_state(response, expected_pid=PID, executable="xr_3da"),
                    expected,
                )

        for name, response in (
            ("duplicate", document({"runningProcesses": [
                {"processIdentifier": PID, "executable": EXECUTABLE},
                {"processIdentifier": PID, "executable": EXECUTABLE},
            ]})),
            ("malformed-unrelated", document({"runningProcesses": [
                {"processIdentifier": PID + 1, "executable": "not-a-file-uri"},
            ]})),
        ):
            with self.subTest(cleanup_state=name):
                with self.assertRaisesRegex(contract.ContractError, "record|file URI"):
                    contract.cleanup_process_state(response, expected_pid=PID, executable="xr_3da")

    def test_running_process_requires_a_safe_local_file_uri(self) -> None:
        for executable, expected in (
            ("/private/var/containers/xr_3da", "file URI"),
            ("https://example.invalid/xr_3da", "local file URI"),
            ("file://localhost/private/var/containers/xr_3da", "without host"),
            ("file:///private/var/containers/xr_3da?query", "without host"),
            ("file:///private/var/containers/%2e%2e/xr_3da", "dot path components"),
            ("file:///private/var/containers/%ZZ/xr_3da", "invalid percent escape"),
            ("file:///private/var/containers/%FF/xr_3da", "not UTF-8"),
            ("file:///private/var/containers/%00xr_3da", "absolute local path"),
        ):
            with self.subTest(executable=executable):
                response = document({"runningProcesses": [{
                    "processIdentifier": PID,
                    "executable": executable,
                }]})
                with self.assertRaisesRegex(contract.ContractError, expected):
                    contract.verify_running_process(response, expected_pid=PID, executable="xr_3da")

        encoded_basename = document({"runningProcesses": [{
            "processIdentifier": PID,
            "executable": "file:///private/var/containers/xr_%33da",
        }]})
        self.assertEqual(
            contract.verify_running_process(encoded_basename, expected_pid=PID, executable="xr_3da"), PID
        )

    def test_unique_executable_pid_is_narrow_and_fail_closed(self) -> None:
        response = document({"runningProcesses": [
            {"processIdentifier": 7, "executable": "file:///usr/libexec/unrelated"},
            {"processIdentifier": PID, "executable": EXECUTABLE},
        ]})
        self.assertEqual(contract.unique_running_executable_pid(response, executable="xr_3da"), PID)

        duplicate = document({"runningProcesses": [
            {"processIdentifier": PID, "executable": EXECUTABLE},
            {"processIdentifier": PID + 1, "executable": "file:///other/xr_3da"},
        ]})
        with self.assertRaisesRegex(contract.ContractError, "exactly one"):
            contract.unique_running_executable_pid(duplicate, executable="xr_3da")

        absent = document({"runningProcesses": [
            {"processIdentifier": 7, "executable": "file:///usr/libexec/unrelated"},
        ]})
        with self.assertRaisesRegex(contract.ContractError, "found 0"):
            contract.unique_running_executable_pid(absent, executable="xr_3da")

    def test_cli_parses_devicectl_json_files(self) -> None:
        launch = self.write_json("launch.json", document({"process": {"processIdentifier": PID}}))
        processes = self.write_json("processes.json", document({"runningProcesses": [{
            "processIdentifier": PID,
            "executable": EXECUTABLE,
        }]}))
        self.assertEqual(contract.command_main(["launch-pid", "--json", str(launch)]), 0)
        self.assertEqual(contract.command_main([
            "running-process", "--json", str(processes), "--pid", str(PID), "--executable", "xr_3da",
        ]), 0)
        self.assertEqual(contract.command_main([
            "cleanup-process-state", "--json", str(processes), "--pid", str(PID),
            "--executable", "xr_3da",
        ]), 0)

    def test_readiness_requires_a_newline_complete_same_pid_activate_marker(self) -> None:
        complete = self.write_log("complete.log", (
            b"old log\n"
            b"* iOS lifecycle v1 pid=99 seq=1 event=activate\n"
            b"* iOS lifecycle v1 pid=4242 seq=2 event=activate\n"
        ))
        self.assertTrue(contract.has_newline_complete_readiness(str(complete), expected_pid=PID))

        partial = self.write_log("partial.log", b"* iOS lifecycle v1 pid=4242 seq=2 event=activate")
        self.assertFalse(contract.has_newline_complete_readiness(str(partial), expected_pid=PID))

        wrong_pid = self.write_log("wrong-pid.log", b"* iOS lifecycle v1 pid=99 seq=2 event=activate\n")
        self.assertFalse(contract.has_newline_complete_readiness(str(wrong_pid), expected_pid=PID))

    def test_gameplay_readiness_additionally_requires_same_pid_resolved_sector(self) -> None:
        activate = b"* iOS lifecycle v1 pid=4242 seq=2 event=activate\n"
        complete = self.write_log("gameplay-ready.log", activate + sector_marker())
        self.assertTrue(contract.has_newline_complete_readiness(
            str(complete), expected_pid=PID, require_resolved_sector=True,
        ))

        for name, content in (
            ("missing-sector", activate),
            ("wrong-sector-pid", activate + sector_marker(pid=99)),
            ("unresolved-sector", activate + sector_marker(status="unresolved")),
            ("partial-sector", activate + sector_marker().rstrip(b"\n")),
            ("missing-activate", sector_marker()),
        ):
            with self.subTest(name=name):
                path = self.write_log(f"{name}.log", content)
                self.assertFalse(contract.has_newline_complete_readiness(
                    str(path), expected_pid=PID, require_resolved_sector=True,
                ))

    def test_lifecycle_sequence_anchor_rejects_invalid_and_nonmonotonic_same_pid_markers(self) -> None:
        valid = self.write_log("anchor.log", (
            b"* iOS lifecycle v1 pid=99 seq=999 event=activate\n"
            b"* iOS lifecycle v1 pid=4242 seq=41 event=activate\n"
            b"* iOS lifecycle v1 pid=4242 seq=42 event=deactivate\n"
        ))
        self.assertEqual(contract.lifecycle_sequence_anchor(str(valid), expected_pid=PID), 42)

        nonmonotonic = self.write_log("anchor-nonmonotonic.log", (
            b"* iOS lifecycle v1 pid=4242 seq=42 event=activate\n"
            b"* iOS lifecycle v1 pid=4242 seq=42 event=deactivate\n"
        ))
        with self.assertRaisesRegex(contract.ContractError, "not strictly increasing"):
            contract.lifecycle_sequence_anchor(str(nonmonotonic), expected_pid=PID)

        invalid = self.write_log("anchor-invalid.log", b"* iOS lifecycle v1 pid=4242 seq=1 event=activate extra\n")
        with self.assertRaisesRegex(contract.ContractError, "invalid versioned lifecycle"):
            contract.lifecycle_sequence_anchor(str(invalid), expected_pid=PID)


if __name__ == "__main__":
    unittest.main()
