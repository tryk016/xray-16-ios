#!/usr/bin/env python3

from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from prepare_scheme import SchemeError, prepare_scheme


def scheme(blueprint_name: str, identifier: str) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<Scheme version="1.3">
  <BuildAction><BuildActionEntries><BuildActionEntry>
    <BuildableReference BlueprintIdentifier="{identifier}" BlueprintName="{blueprint_name}"/>
  </BuildActionEntry></BuildActionEntries></BuildAction>
  <TestAction><Testables/><AdditionalOptions/></TestAction>
</Scheme>
'''


class PrepareSchemeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "Runner.xcodeproj"
        self.schemes = self.project / "xcshareddata" / "xcschemes"
        self.schemes.mkdir(parents=True)
        (self.schemes / "OpenXRayUITests.xcscheme").write_text(
            scheme("OpenXRayUITests", "TEST-ID"), encoding="utf-8"
        )
        (self.schemes / "AutomationHost.xcscheme").write_text(
            scheme("AutomationHost", "HOST-ID"), encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_prepares_testable_and_host_idempotently(self) -> None:
        prepare_scheme(self.project)
        prepare_scheme(self.project)
        root = ET.parse(self.schemes / "OpenXRayUITests.xcscheme").getroot()
        testables = root.findall("./TestAction/Testables/TestableReference/BuildableReference")
        hosts = root.findall("./TestAction/MacroExpansion/BuildableReference")
        self.assertEqual([item.get("BlueprintIdentifier") for item in testables], ["TEST-ID"])
        self.assertEqual([item.get("BlueprintIdentifier") for item in hosts], ["HOST-ID"])

    def test_rejects_missing_generated_target(self) -> None:
        (self.schemes / "AutomationHost.xcscheme").write_text(
            scheme("WrongHost", "HOST-ID"), encoding="utf-8"
        )
        with self.assertRaisesRegex(SchemeError, "AutomationHost"):
            prepare_scheme(self.project)


if __name__ == "__main__":
    unittest.main()
