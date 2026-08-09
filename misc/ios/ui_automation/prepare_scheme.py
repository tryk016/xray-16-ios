#!/usr/bin/env python3
"""Make CMake's Xcode scheme runnable as an iOS UI-test scheme."""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


TEST_SCHEME = "OpenXRayUITests.xcscheme"
HOST_SCHEME = "AutomationHost.xcscheme"


class SchemeError(RuntimeError):
    pass


def _buildable_reference(root: ET.Element, blueprint_name: str) -> ET.Element:
    for reference in root.findall("./BuildAction/BuildActionEntries/BuildActionEntry/BuildableReference"):
        if reference.get("BlueprintName") == blueprint_name:
            return deepcopy(reference)
    raise SchemeError(f"scheme has no BuildableReference for {blueprint_name}")


def prepare_scheme(project: Path) -> Path:
    schemes = project / "xcshareddata" / "xcschemes"
    test_path = schemes / TEST_SCHEME
    host_path = schemes / HOST_SCHEME
    if not test_path.is_file() or not host_path.is_file():
        raise SchemeError(f"expected generated schemes under {schemes}")

    test_tree = ET.parse(test_path)
    test_root = test_tree.getroot()
    host_root = ET.parse(host_path).getroot()
    test_reference = _buildable_reference(test_root, "OpenXRayUITests")
    host_reference = _buildable_reference(host_root, "AutomationHost")

    test_action = test_root.find("./TestAction")
    if test_action is None:
        raise SchemeError("UI-test scheme has no TestAction")

    testables = test_action.find("./Testables")
    if testables is None:
        testables = ET.Element("Testables")
        test_action.insert(0, testables)
    testables.clear()
    testable = ET.SubElement(testables, "TestableReference", {"skipped": "NO", "parallelizable": "NO"})
    testable.append(test_reference)

    macro_expansion = test_action.find("./MacroExpansion")
    if macro_expansion is None:
        additional_options = test_action.find("./AdditionalOptions")
        insert_at = list(test_action).index(additional_options) if additional_options is not None else len(test_action)
        macro_expansion = ET.Element("MacroExpansion")
        test_action.insert(insert_at, macro_expansion)
    macro_expansion.clear()
    macro_expansion.append(host_reference)

    ET.indent(test_tree, space="   ")
    test_tree.write(test_path, encoding="UTF-8", xml_declaration=True, short_empty_elements=True)
    return test_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path, help="generated .xcodeproj directory")
    args = parser.parse_args()
    try:
        path = prepare_scheme(args.project.resolve())
    except (OSError, ET.ParseError, SchemeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(f"prepared: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
