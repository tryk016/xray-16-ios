#!/usr/bin/env python3
"""Mutation-backed host contract for the iOS lifecycle transition marker."""

from __future__ import annotations

from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
XRAY_CPP = REPO_ROOT / "src/xrEngine/x_ray.cpp"
LIFECYCLE_STATE = REPO_ROOT / "src/xrEngine/ios/ios_lifecycle_state.h"
MARKER_GRAMMAR = "* iOS lifecycle v1 pid=%d seq=%llu event=%s"


def function_body(source: str, name: str) -> str:
    signature = re.search(rf"\b{name}\s*\(", source)
    if signature is None:
        raise AssertionError(f"{name} signature not found")

    opening = source.find("{", signature.end())
    if opening < 0:
        raise AssertionError(f"{name} body not found")

    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1:index]
    raise AssertionError(f"{name} body is not balanced")


def ios_regions(source: str) -> list[str]:
    lines = source.splitlines(keepends=True)
    regions: list[str] = []
    offset = 0
    for start_index, line in enumerate(lines):
        if not re.match(r"\s*#if\s+defined\(XR_PLATFORM_APPLE_IOS\)", line):
            offset += len(line)
            continue

        depth = 0
        end = offset
        for candidate in lines[start_index:]:
            if re.match(r"\s*#if\b", candidate):
                depth += 1
            elif re.match(r"\s*#endif\b", candidate):
                depth -= 1
            end += len(candidate)
            if depth == 0:
                regions.append(source[offset:end])
                break
        else:
            raise AssertionError("XR_PLATFORM_APPLE_IOS implementation guard is not balanced")
        offset += len(line)
    if not regions:
        raise AssertionError("XR_PLATFORM_APPLE_IOS implementation guard not found")
    return regions


def marker_errors(source: str, policy: str) -> list[str]:
    errors: list[str] = []
    try:
        process = function_body(source, "ProcessIOSLifecycleEvents")
        reporter = function_body(source, "ReportIOSLifecycleTransition")
        helper = function_body(policy, "ShouldReportProcessedTransition")
        regions = ios_regions(source)
    except AssertionError as error:
        return [str(error)]

    if not any('#include "ios/ios_lifecycle_state.h"' in region for region in regions):
        errors.append("lifecycle marker policy include must be iOS-only")
    if not any("#include <unistd.h>" in region for region in regions):
        errors.append("getpid declaration must be included only for iOS")
    if not any(
        "ProcessIOSLifecycleEvents" in region
        and "ReportIOSLifecycleTransition" in region
        and MARKER_GRAMMAR in region
        for region in regions
    ):
        errors.append("lifecycle marker implementation must be iOS-only")

    apply = process.find("ios_lifecycle::ApplyActivityOrdered(")
    policy_call = re.search(r"ios_lifecycle::ShouldReportProcessedTransition\s*\((?P<args>[^)]*)\)", process)
    report_call = re.search(r"\bReportIOSLifecycleTransition\s*\(\s*focusAfter\s*\)", process)
    if apply < 0 or policy_call is None or report_call is None or not apply < policy_call.start() < report_call.start():
        errors.append("marker must report the processed transition after ApplyActivityOrdered")

    focus_before = re.search(r"const\s+bool\s+focusBefore\s*=\s*Device\.b_is_InFocus\s*;", process)
    focus_after = re.search(r"const\s+bool\s+focusAfter\s*=\s*Device\.b_is_InFocus\s*;", process)
    applied = re.search(r"bool\s+activityApplied\s*=\s*false\s*;", process)
    arguments = [] if policy_call is None else [argument.strip() for argument in policy_call.group("args").split(",")]
    if (
        None in (focus_before, focus_after, applied, policy_call)
        or len(arguments) != 4
        or arguments[0] != "activityApplied"
        or arguments[1] != "focusBefore"
        or "focusAfter" not in arguments
        or not any(argument in {"requestedForeground", "events.foreground"} for argument in arguments)
    ):
        errors.append("marker policy must receive activityApplied and focus before/after/requested values")
    elif not (focus_before.start() < apply < focus_after.start() < policy_call.start()):
        errors.append("focus snapshots must bracket ApplyActivityOrdered before reporting")

    if policy_call is None or "activityApplied = true;" not in process[apply:policy_call.start()]:
        errors.append("activity callback must record that ApplyActivityOrdered actually applied work")

    if re.search(r"events\.activity\s*!=\s*ios_lifecycle::ActivityChange::None", process):
        errors.append("marker must allow a focus-retry without events.activity")

    marker = reporter.find(MARKER_GRAMMAR)
    if marker < 0:
        errors.append("marker grammar must be exact and versioned")
    pid = re.search(r"\bgetpid\s*\(\s*\)", reporter)
    positive_pid = re.search(r"\bpid\s*>\s*0\b", reporter)
    reject_nonpositive = re.search(r"if\s*\(\s*pid\s*<=\s*0\s*\)\s*return\s*;", reporter)
    pid_guard = positive_pid if positive_pid is not None else reject_nonpositive
    if pid is None or pid_guard is None or pid_guard.start() > marker:
        errors.append("marker must require a positive getpid before Msg")

    event_name = re.search(
        r"\bforeground\s*\?\s*\"activate\"\s*:\s*\"deactivate\"",
        reporter,
    )
    if event_name is None:
        errors.append("marker event vocabulary must select activate/deactivate from requested foreground")

    sequence = re.search(r"static\s+u64\s+(?P<name>\w+)\s*=\s*0\s*;", reporter)
    increment = None
    if sequence is not None:
        name = re.escape(sequence.group("name"))
        increment = re.search(rf"(?:\+\+\s*{name}|{name}\s*\+=\s*1)\b", reporter)
    if sequence is None or increment is None or not sequence.start() < increment.start() < marker:
        errors.append("marker must increment a static u64 sequence immediately before Msg")
    elif re.search(
        rf"(?:\+\+\s*{re.escape(sequence.group('name'))}|{re.escape(sequence.group('name'))}\s*\+=\s*1)\b",
        reporter[marker:],
    ):
        errors.append("marker sequence must not increment after Msg")

    flush = reporter.find("FlushLog();", marker)
    if flush < 0:
        errors.append("marker must FlushLog after Msg")

    expected_helper = (
        "return activityApplied && focusBefore != focusAfter && focusAfter == requestedForeground;"
    )
    if expected_helper not in helper:
        errors.append("processed-transition policy must require applied, changed focus, and requested final focus")
    return errors


class LifecycleMarkerContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = XRAY_CPP.read_text(encoding="utf-8")
        cls.policy = LIFECYCLE_STATE.read_text(encoding="utf-8")

    def assert_contract_passes(self, source: str, policy: str | None = None) -> None:
        self.assertEqual(marker_errors(source, self.policy if policy is None else policy), [])

    def assert_contract_fails(self, source: str, expected: str, policy: str | None = None) -> None:
        errors = marker_errors(source, self.policy if policy is None else policy)
        self.assertIn(expected, errors, errors)

    def setUp(self) -> None:
        if self._testMethodName != "test_baseline" and marker_errors(self.source, self.policy):
            self.skipTest("production lifecycle marker has not landed yet")

    def test_baseline(self) -> None:
        self.assert_contract_passes(self.source)

    def test_marker_before_processed_activity_fails(self) -> None:
        moved = self.source.replace(
            "    ios_lifecycle::ApplyActivityOrdered(",
            "    ReportIOSLifecycleTransition(focusAfter);\n"
            "    ios_lifecycle::ApplyActivityOrdered(",
            1,
        )
        self.assert_contract_fails(moved, "marker must report the processed transition after ApplyActivityOrdered")

    def test_rollback_guard_broadening_fails(self) -> None:
        self.assert_contract_fails(
            self.source,
            "processed-transition policy must require applied, changed focus, and requested final focus",
            self.policy.replace("focusAfter == requestedForeground", "true", 1),
        )

    def test_dedupe_removal_fails(self) -> None:
        self.assert_contract_fails(
            self.source,
            "processed-transition policy must require applied, changed focus, and requested final focus",
            self.policy.replace("focusBefore != focusAfter", "true", 1),
        )

    def test_retry_allowance_removal_fails(self) -> None:
        mutated = self.source.replace(
            "if (ios_lifecycle::ShouldReportProcessedTransition(",
            "if (events.activity != ios_lifecycle::ActivityChange::None\n"
            "        && ios_lifecycle::ShouldReportProcessedTransition(",
            1,
        )
        self.assert_contract_fails(mutated, "marker must allow a focus-retry without events.activity")

    def test_positive_pid_removal_fails(self) -> None:
        mutated, replacements = re.subn(
            r"\bpid\s*<=\s*0\b", "pid < 0", self.source, count=1
        )
        if replacements == 0:
            mutated, replacements = re.subn(
                r"\bpid\s*>\s*0\b", "pid >= 0", self.source, count=1
            )
        self.assertEqual(replacements, 1)
        self.assert_contract_fails(
            mutated,
            "marker must require a positive getpid before Msg",
        )

    def test_sequence_increment_removal_fails(self) -> None:
        sequence = re.search(r"static\s+u64\s+(?P<name>\w+)\s*=\s*0\s*;", self.source)
        self.assertIsNotNone(sequence)
        assert sequence is not None
        name = sequence.group("name")
        increment = re.search(rf"(?:\+\+\s*{name}|{name}\s*\+=\s*1)\b", self.source)
        self.assertIsNotNone(increment)
        assert increment is not None
        self.assert_contract_fails(
            self.source[:increment.start()] + name + self.source[increment.end():],
            "marker must increment a static u64 sequence immediately before Msg",
        )

    def test_sequence_increment_after_msg_fails(self) -> None:
        sequence = re.search(r"static\s+u64\s+(?P<name>\w+)\s*=\s*0\s*;", self.source)
        self.assertIsNotNone(sequence)
        assert sequence is not None
        marker = self.source.index(MARKER_GRAMMAR)
        flush = self.source.index("FlushLog();", marker) + len("FlushLog();")
        mutated = self.source[:flush] + f"\n    ++{sequence.group('name')};" + self.source[flush:]
        self.assert_contract_fails(mutated, "marker sequence must not increment after Msg")

    def test_grammar_regression_fails(self) -> None:
        self.assert_contract_fails(
            self.source.replace("* iOS lifecycle v1", "* iOS lifecycle", 1),
            "marker grammar must be exact and versioned",
        )

    def test_flush_removal_fails(self) -> None:
        marker = self.source.index(MARKER_GRAMMAR)
        flush = self.source.index("FlushLog();", marker)
        mutated = self.source[:flush] + self.source[flush:].replace("FlushLog();\n", "", 1)
        self.assert_contract_fails(mutated, "marker must FlushLog after Msg")


if __name__ == "__main__":
    unittest.main(verbosity=2)
