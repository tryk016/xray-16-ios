#!/usr/bin/env python3
"""Pure source-level contract for the deterministic system-URL lifecycle stimulus.

This suite reads and mutates XCTest source text only.  It never invokes Xcode,
the UI-automation runner, a Simulator, or a physical device.
"""

from __future__ import annotations

from pathlib import Path
import re
import unittest


SOURCE_PATH = Path(__file__).with_name("OpenXRayUITests.m")
LIFECYCLE_SIGNATURE = "- (BOOL)runFiveAppSwitchCyclesForOpenXRay:"


class ContractError(AssertionError):
    pass


def method_body(source: str, signature: str) -> str:
    start = source.find(signature)
    if start < 0:
        raise ContractError(f"missing method: {signature}")
    opening_brace = source.find("{", start)
    if opening_brace < 0:
        raise ContractError(f"missing method body: {signature}")

    depth = 0
    for index in range(opening_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening_brace + 1:index]
    raise ContractError(f"unterminated method body: {signature}")


def loop_body(method: str) -> str:
    match = re.search(
        r"for\s*\(\s*NSUInteger\s+cycle\s*=\s*1\s*;\s*cycle\s*<=\s*5\s*;\s*\+\+cycle\s*\)",
        method,
    )
    if match is None:
        raise ContractError("missing exact five-cycle loop")
    opening_brace = method.find("{", match.end())
    if opening_brace < 0:
        raise ContractError("missing five-cycle loop body")

    depth = 0
    for index in range(opening_brace, len(method)):
        if method[index] == "{":
            depth += 1
        elif method[index] == "}":
            depth -= 1
            if depth == 0:
                return method[opening_brace + 1:index]
    raise ContractError("unterminated five-cycle loop body")


def one_match(pattern: str, text: str, description: str) -> int:
    matches = list(re.finditer(pattern, text, re.DOTALL))
    if len(matches) != 1:
        raise ContractError(f"{description}: expected exactly one match, got {len(matches)}")
    return matches[0].start()


def require_ordered_steps(body: str) -> None:
    steps = (
        (
            r"\[\s*\[\s*XCUIDevice\s+sharedDevice\s*\]\s*\.\s*system\s+"
            r"openURL\s*:\s*lifecycleURL\s*\]\s*;",
            "system openURL stimulus",
        ),
        (
            r"\[\s*self\s+requireApplication\s*:\s*browser\s+reachesState\s*:\s*"
            r"XCUIApplicationStateRunningForeground\b",
            "default-browser foreground barrier",
        ),
        (
            r"\[\s*NSThread\s+sleepForTimeInterval\s*:\s*1\.0\s*\]\s*;",
            "exact one-second lifecycle settle",
        ),
        (r"\[\s*app\s+activate\s*\]\s*;", "OpenXRay recovery activate"),
        (
            r"\[\s*self\s+requireApplication\s*:\s*app\s+reachesState\s*:\s*"
            r"XCUIApplicationStateRunningForeground\b",
            "OpenXRay foreground recovery barrier",
        ),
        (
            r"\[\s*NSThread\s+sleepForTimeInterval\s*:\s*3\.0\s*\]\s*;",
            "exact three-second screenshot settle",
        ),
        (
            r"\[\s*self\s+attachScreenNamed\s*:\s*\[\s*NSString\s+"
            r"stringWithFormat\s*:\s*@\"%@-cycle-%lu\"\s*,\s*"
            r"attachmentPrefix\s*,\s*\(\s*unsigned\s+long\s*\)\s*cycle\s*\]\s*\]\s*;",
            "per-cycle screenshot",
        ),
    )
    positions = [one_match(pattern, body, description) for pattern, description in steps]
    if positions != sorted(positions):
        raise ContractError("system-URL lifecycle barriers are not in deterministic order")


def require_unique_local_url(body: str) -> None:
    one_match(
        r"NSURL\s*\*\s*const\s+lifecycleURL\s*=\s*\[\s*NSURL\s+"
        r"URLWithString\s*:\s*\[\s*NSString\s+stringWithFormat\s*:\s*"
        r"@\"http://127\.0\.0\.1:9/openxray-lifecycle\?cycle=%lu\"\s*,\s*"
        r"\(\s*unsigned\s+long\s*\)\s*cycle\s*\]\s*\]\s*;",
        body,
        "unique cycle-indexed local HTTP URL",
    )
    if re.search(r"lifecycleURL\s*==\s*nil", body) is None:
        raise ContractError("local lifecycle URL lacks a fail-closed nil guard")


def require_no_shortcuts(body: str) -> None:
    forbidden = (
        (r"\[\s*browser\s+(?:terminate|launch|activate)\s*\]", "browser lifecycle shortcut"),
        (r"\bsafari\b|safariApplication|com\.apple\.mobilesafari", "legacy Safari stimulus"),
        (
            r"\[\s*self\s+requireApplicationBackgrounded\s*:\s*app\s+"
            r"applicationName\s*:\s*@\"OpenXRay\"",
            "proven-false OpenXRay background-state barrier",
        ),
        (
            r"\[\s*self\s+requireApplicationBackgrounded\s*:\s*browser\b",
            "proven-false default-browser background-state barrier",
        ),
        (r"\[\s*app\s+(?:launch|terminate)\s*\]", "OpenXRay launch/terminate in cycle"),
        (r"XCUIDeviceButtonHome|pressButton\s*:", "Home-button stimulus"),
        (r"\[\s*XCUIDevice\s+sharedDevice\s*\]\s*\.\s*system\s+openURL\s*:\s*(?!lifecycleURL\b)",
            "non-contract system URL stimulus"),
        (r"\[\s*browser\s+(?:openURL|open)\s*:", "browser URL stimulus"),
        (r"\bretry\b", "retry stimulus"),
    )
    for pattern, description in forbidden:
        if re.search(pattern, body, re.IGNORECASE):
            raise ContractError(f"forbidden {description}")


def require_default_browser_helper(source: str) -> None:
    helper = method_body(source, "- (XCUIApplication*)defaultBrowserApplication")
    one_match(
        r"initWithBundleIdentifier\s*:\s*@\"org\.mozilla\.ios\.Firefox\"",
        helper,
        "Firefox default-browser bundle",
    )
    if re.search(r"safariApplication|com\.apple\.mobilesafari", source, re.IGNORECASE):
        raise ContractError("legacy Safari proxy remains in XCTest source")


def require_general_background_helper(source: str) -> None:
    helper = method_body(source, "- (BOOL)requireApplicationBackgrounded:")
    if "applicationName:(NSString*)applicationName" not in source[source.find("- (BOOL)requireApplicationBackgrounded:"):]:
        raise ContractError("background helper is not application-general")
    for token in (
        "XCUIApplicationStateRunningBackground",
        "XCUIApplicationStateRunningBackgroundSuspended",
        "XCUIApplicationStateNotRunning",
        "applicationName",
    ):
        if token not in helper:
            raise ContractError(f"background helper missing {token}")
    if '@"OpenXRay"' in helper:
        raise ContractError("background helper is OpenXRay-specific")


def require_autonomous_process_ownership(source: str, test_signature: str) -> None:
    test = method_body(source, test_signature)
    if re.search(r"\[\s*app\s+(?:launch|terminate)\s*\]", test):
        raise ContractError(
            f"{test_signature} launches or terminates OpenXRay; run.sh owns autonomous cleanup"
        )


def validate(source: str) -> None:
    lifecycle = method_body(source, LIFECYCLE_SIGNATURE)
    cycle = loop_body(lifecycle)
    if lifecycle[:lifecycle.find(cycle)].count("[self defaultBrowserApplication]") != 0:
        raise ContractError("default-browser proxy is retained outside the cycle")
    if one_match(
        r"XCUIApplication\s*\*\s*const\s+browser\s*=\s*\[\s*self\s+defaultBrowserApplication\s*\]\s*;",
        cycle,
        "fresh default-browser proxy",
    ) < 0:
        raise ContractError("missing fresh default-browser proxy")
    require_default_browser_helper(source)
    require_unique_local_url(cycle)
    require_ordered_steps(cycle)
    require_no_shortcuts(cycle)
    require_general_background_helper(source)
    for signature in (
        "- (void)testLifecycleFiveAppSwitchCycles",
        "- (void)testAudioInterruptionWhileOpenXRayForeground",
        "- (void)testReliabilityFiveAppSwitchCyclesAndForegroundAudioInterruption",
    ):
        require_autonomous_process_ownership(source, signature)


def replace_in_lifecycle(source: str, old: str, new: str) -> str:
    start = source.find(LIFECYCLE_SIGNATURE)
    end = start + len(method_body(source, LIFECYCLE_SIGNATURE))
    # Include the method's closing brace and mutate only this lifecycle helper.
    end = source.find("\n}\n", end) + 3
    segment = source[start:end]
    if old not in segment:
        raise AssertionError(f"mutation target missing: {old!r}")
    return source[:start] + segment.replace(old, new, 1) + source[end:]


def replace_in_test(source: str, signature: str, old: str, new: str) -> str:
    start = source.find(signature)
    if start < 0:
        raise AssertionError(f"mutation test target missing: {signature}")
    opening_brace = source.find("{", start)
    body = method_body(source, signature)
    body_start = opening_brace + 1
    body_end = body_start + len(body)
    if old not in body:
        raise AssertionError(f"mutation target missing: {old!r}")
    return source[:body_start] + body.replace(old, new, 1) + source[body_end:]
