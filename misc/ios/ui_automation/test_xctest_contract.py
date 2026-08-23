#!/usr/bin/env python3
"""Mutation tests for the deterministic system-URL lifecycle source contract."""

import os as _test_feedback_os
if _test_feedback_os.environ.get("XRAY_FEEDBACK_RAW_EVENT_FD"):
    try:
        import sys as _test_feedback_sys
        _test_feedback_sys.path.insert(
            0, _test_feedback_os.path.dirname(_test_feedback_os.path.dirname(__file__)))
        import test_feedback_unittest as _test_feedback_unittest
        _test_feedback_unittest.install_from_environment(
            "python::misc/ios/ui_automation/test_xctest_contract.py",
            _test_feedback_sys.argv,
        )
    except BaseException:
        pass

from pathlib import Path
import unittest

from xctest_contract import ContractError, replace_in_lifecycle, replace_in_test, validate


SOURCE_PATH = Path(__file__).with_name("OpenXRayUITests.m")


class XCTestLifecycleContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SOURCE_PATH.read_text(encoding="utf-8")

    def assertContractRejected(self, mutated: str) -> None:
        with self.assertRaises(ContractError):
            validate(mutated)

    def test_current_source_satisfies_deterministic_safari_contract(self) -> None:
        validate(self.source)

    def test_rejects_warm_activate_instead_of_fresh_safari_launch(self) -> None:
        stimulus = "[[XCUIDevice sharedDevice].system openURL:lifecycleURL];"
        for legacy_stimulus in (
            "[browser terminate];",
            "[browser launch];",
            "[browser activate];",
            "[safari activate];",
            "[[XCUIDevice sharedDevice] pressButton:XCUIDeviceButtonHome];",
        ):
            self.assertContractRejected(
                replace_in_lifecycle(self.source, stimulus, f"{legacy_stimulus}\n        {stimulus}")
            )
        self.assertContractRejected(
            self.source.replace("org.mozilla.ios.Firefox", "com.apple.mobilesafari", 1)
        )
        for test_signature in (
            "- (void)testLifecycleFiveAppSwitchCycles",
            "- (void)testAudioInterruptionWhileOpenXRayForeground",
            "- (void)testReliabilityFiveAppSwitchCyclesAndForegroundAudioInterruption",
        ):
            self.assertContractRejected(
                replace_in_test(
                    self.source,
                    test_signature,
                    "XCUIApplication* app = [self openXRayApplication];",
                    "XCUIApplication* app = [self openXRayApplication];\n"
                    "    [self addTeardownBlock:^{\n"
                    "        [app terminate];\n"
                    "    }];",
                )
            )

    def test_rejects_missing_not_running_barrier(self) -> None:
        self.assertContractRejected(
            replace_in_lifecycle(
                self.source,
                "cycle <= 5",
                "cycle <= 4",
            )
        )
        self.assertContractRejected(
            replace_in_lifecycle(
                self.source,
                "http://127.0.0.1:9/openxray-lifecycle?cycle=%lu",
                "http://127.0.0.1:9/openxray-lifecycle?cycle=1",
            )
        )
        settle = "[NSThread sleepForTimeInterval:1.0];"
        for replacement in (
            "[NSThread sleepForTimeInterval:0.0];",
            "[NSThread sleepForTimeInterval:2.0];",
            "",
        ):
            self.assertContractRejected(
                replace_in_lifecycle(self.source, settle, replacement)
            )
        self.assertContractRejected(
            replace_in_lifecycle(
                self.source,
                settle,
                "[self requireApplicationBackgrounded:app "
                "applicationName:@\"OpenXRay\" timeout:10.0 stage:@\"legacy\"];\n"
                "        " + settle,
            )
        )

    def test_rejects_reordered_terminate_and_launch(self) -> None:
        stimulus = "[[XCUIDevice sharedDevice].system openURL:lifecycleURL];"
        reordered = replace_in_lifecycle(
            self.source,
            stimulus,
            "[browser cycleOrderSentinel];",
        )
        reordered = replace_in_lifecycle(
            reordered,
            "stage:browserForegroundStage])\n            return NO;",
            "stage:browserForegroundStage])\n            return NO;\n\n        " + stimulus,
        )
        self.assertContractRejected(
            replace_in_lifecycle(reordered, "[browser cycleOrderSentinel];", "[NSThread sleepForTimeInterval:0.5];")
        )

    def test_rejects_missing_safari_background_recovery_barrier(self) -> None:
        screenshot_settle = "[NSThread sleepForTimeInterval:3.0];"
        self.assertContractRejected(
            replace_in_lifecycle(
                self.source,
                screenshot_settle,
                "[self requireApplicationBackgrounded:browser "
                "applicationName:@\"Default browser (Firefox)\" "
                "timeout:10.0 stage:@\"legacy\"];\n"
                "        " + screenshot_settle,
            )
        )
        self.assertContractRejected(
            replace_in_lifecycle(
                self.source,
                screenshot_settle,
                "[NSThread sleepForTimeInterval:2.0];",
            )
        )
        self.assertContractRejected(
            replace_in_lifecycle(
                self.source,
                "[self attachScreenNamed:",
                "[self skipScreenNamed:",
            )
        )


if __name__ == "__main__":
    unittest.main()
