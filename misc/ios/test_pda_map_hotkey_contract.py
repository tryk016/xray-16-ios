#!/usr/bin/env python3
"""Mutation-backed source contract for the CoP PDA map hotkey fallback."""

from __future__ import annotations

import os as _test_feedback_os
if _test_feedback_os.environ.get("XRAY_FEEDBACK_RAW_EVENT_FD"):
    try:
        import sys as _test_feedback_sys
        import test_feedback_unittest as _test_feedback_unittest
        _test_feedback_unittest.install_from_environment("python::misc/ios/test_pda_map_hotkey_contract.py", _test_feedback_sys.argv)
    except BaseException:
        pass

from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
PDA_WINDOW = REPO_ROOT / "src/xrGame/ui/UIPdaWnd.cpp"


def show_map_wnd_body(source: str) -> str:
    signature = re.search(r"void\s+CUIPdaWnd::Show_MapWnd\s*\(\s*bool\s+status\s*\)", source)
    if signature is None:
        raise AssertionError("CUIPdaWnd::Show_MapWnd signature not found")
    opening = source.find("{", signature.end())
    if opening < 0:
        raise AssertionError("CUIPdaWnd::Show_MapWnd body not found")
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1:index]
    raise AssertionError("CUIPdaWnd::Show_MapWnd body is not balanced")


def contract_errors(body: str) -> list[str]:
    errors: list[str] = []
    disabled = re.search(r"if\s*\(\s*!status\s*\)\s*return\s*;", body)
    if disabled is None:
        errors.append("Show_MapWnd(false) must be a no-op")

    map_choice = re.search(
        r"if\s*\(\s*pUIMapWnd\s*\)\s*section\s*=\s*\"eptMap\"\s*;",
        body,
    )
    task_choice = re.search(
        r"else\s+if\s*\(\s*pUITaskWnd\s*\)\s*section\s*=\s*\"eptTasks\"\s*;",
        body,
    )
    if map_choice is None:
        errors.append("Show_MapWnd must prefer standalone eptMap when pUIMapWnd exists")
    if task_choice is None:
        errors.append("Show_MapWnd must fall back to CoP eptTasks when pUITaskWnd exists")
    if map_choice is not None and task_choice is not None and map_choice.start() >= task_choice.start():
        errors.append("Show_MapWnd must give standalone eptMap priority over eptTasks")

    no_window = re.search(r"if\s*\(\s*!section\s*\)\s*return\s*;", body)
    if no_window is None:
        errors.append("Show_MapWnd must be a no-op when neither map surface exists")

    set_dialog = body.find("SetActiveSubdialog(section);")
    set_tab = body.find("UITabControl->SetActiveTab(section);")
    if set_dialog < 0:
        errors.append("Show_MapWnd must select the resolved active dialog")
    if set_tab < 0:
        errors.append("Show_MapWnd must synchronize the resolved tab")
    if (no_window is not None and set_dialog >= 0 and set_tab >= 0
            and not (no_window.end() <= set_dialog < set_tab)):
        errors.append("Show_MapWnd must select and synchronize only after a surface is resolved")
    return errors


class PdaMapHotkeyContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.body = show_map_wnd_body(PDA_WINDOW.read_text(encoding="utf-8"))

    def assert_contract_passes(self, body: str) -> None:
        self.assertEqual(contract_errors(body), [])

    def assert_contract_fails(self, body: str, expected: str) -> None:
        errors = contract_errors(body)
        self.assertIn(expected, errors, errors)

    def test_baseline(self) -> None:
        self.assert_contract_passes(self.body)

    def test_false_status_regression_fails(self) -> None:
        self.assert_contract_fails(
            self.body.replace("if (!status)\n        return;\n\n", "", 1),
            "Show_MapWnd(false) must be a no-op",
        )

    def test_standalone_map_priority_regression_fails(self) -> None:
        self.assert_contract_fails(
            self.body.replace('section = "eptMap";', 'section = "eptTasks";', 1),
            "Show_MapWnd must prefer standalone eptMap when pUIMapWnd exists",
        )

    def test_cop_task_fallback_regression_fails(self) -> None:
        self.assert_contract_fails(
            self.body.replace('else if (pUITaskWnd)\n        section = "eptTasks";', "", 1),
            "Show_MapWnd must fall back to CoP eptTasks when pUITaskWnd exists",
        )

    def test_tab_sync_regression_fails(self) -> None:
        self.assert_contract_fails(
            self.body.replace("    UITabControl->SetActiveTab(section);\n", "", 1),
            "Show_MapWnd must synchronize the resolved tab",
        )

    def test_no_window_noop_regression_fails(self) -> None:
        self.assert_contract_fails(
            self.body.replace("if (!section)\n        return;\n\n", "", 1),
            "Show_MapWnd must be a no-op when neither map surface exists",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
