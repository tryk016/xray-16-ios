#!/usr/bin/env python3
"""Mutation-backed static contract for CLocatorAPI::Register path handling."""

from __future__ import annotations

import os as _test_feedback_os
if _test_feedback_os.environ.get("XRAY_FEEDBACK_RAW_EVENT_FD"):
    try:
        import sys as _test_feedback_sys
        import test_feedback_unittest as _test_feedback_unittest
        _test_feedback_unittest.install_from_environment("python::misc/ios/test_locator_registration_contract.py", _test_feedback_sys.argv)
    except BaseException:
        pass

import os
from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
LOCATOR_API = REPO_ROOT / "src/xrCore/LocatorAPI.cpp"
XR_TYPES = REPO_ROOT / "src/xrCore/xr_types.h"


def register_body(source: str) -> str:
    signature = re.search(
        r"const\s+CLocatorAPI::file\s*\*\s*CLocatorAPI::Register\s*\(", source
    )
    if signature is None:
        raise AssertionError("CLocatorAPI::Register signature not found")

    opening = source.find("{", signature.end())
    if opening < 0:
        raise AssertionError("CLocatorAPI::Register body not found")

    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1:index]
    raise AssertionError("CLocatorAPI::Register body is not balanced")


def source_string_path_capacity() -> int:
    xr_types = XR_TYPES.read_text(encoding="utf-8")
    if not re.search(r"using\s+string_path\s*=\s*char\s*\[\s*2\s*\*\s*max_path\s*\]\s*;", xr_types):
        raise AssertionError("string_path must remain char[2 * max_path]")

    try:
        max_path = os.pathconf(REPO_ROOT, "PC_PATH_MAX")
    except (AttributeError, OSError, ValueError) as error:
        raise AssertionError(f"could not determine POSIX PATH_MAX: {error}") from error
    if max_path <= 0:
        raise AssertionError("POSIX PATH_MAX must be positive")
    return 2 * max_path


def contract_errors(body: str) -> list[str]:
    errors: list[str] = []
    declaration = re.search(r"\bstring_path\s+temp_file_name\s*;", body)
    if declaration is None:
        errors.append("Register must declare temp_file_name as string_path")
    if re.search(r"\bstring256\s+temp_file_name\s*;", body):
        errors.append("Register must not narrow temp_file_name to string256")

    empty_check = re.search(
        r"CHECK_OR_EXIT\s*\(\s*name\s*&&\s*\*name\s*,", body
    )
    if empty_check is None:
        errors.append("Register must reject an empty filesystem path")

    copy = re.search(
        r"\b(?:const\s+)?int\s+(?P<result>copy_error)\s*=\s*"
        r"xr_strcpy\s*\(\s*temp_file_name\s*,\s*sizeof\s*\(?\s*temp_file_name\s*\)?\s*,\s*name\s*\)\s*;",
        body,
    )
    if copy is None:
        errors.append("Register must retain xr_strcpy's result for temp_file_name")
        return errors

    copy_check = re.search(
        r"CHECK_OR_EXIT\s*\(\s*copy_error\s*==\s*0\s*,", body
    )
    if copy_check is None:
        errors.append("Register must fail explicitly when xr_strcpy cannot fit the path")
    else:
        guarded_uses = (
            "xr_fs_strlwr(temp_file_name)",
            "restore_path_separators(temp_file_name)",
            "desc.name = temp_file_name",
            "m_files.find(desc)",
            "m_files.insert(desc)",
        )
        first_use = min(
            (body.find(use) for use in guarded_uses if body.find(use) >= 0),
            default=-1,
        )
        if not (copy.start() < copy_check.start() and (first_use < 0 or copy_check.start() < first_use)):
            errors.append("Register must check xr_strcpy before normalizing or registering the path")

    prefix_end = body.find("xr_fs_strlwr(temp_file_name)")
    prefix = body if prefix_end < 0 else body[:prefix_end]
    if re.search(r"\b(?:strn?cpy|SDL_strlcpy|strlcpy)\s*\(", prefix):
        errors.append("Register must not truncate temp_file_name before validation")
    if re.search(r"\breturn\s+nullptr\s*;", body):
        errors.append("Register must not silently return nullptr for a rejected path")

    duplicate = body.find("desc.name = xr_strdup(desc.name);")
    insertion = body.find("m_files.insert(desc)")
    if duplicate < 0 or insertion < 0 or duplicate > insertion:
        errors.append("Register must retain xr_strdup before inserting a new file entry")
    return errors


def copy_error_guard_span(body: str) -> tuple[int, int]:
    start = body.find("CHECK_OR_EXIT(copy_error == 0,")
    if start < 0:
        raise AssertionError("copy-error guard not found")
    end = body.find(";", start)
    if end < 0:
        raise AssertionError("copy-error guard terminator not found")
    return start, end + 1


class LocatorRegistrationContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.body = register_body(LOCATOR_API.read_text(encoding="utf-8"))

    def assert_contract_passes(self, body: str) -> None:
        self.assertEqual(contract_errors(body), [])

    def assert_contract_fails(self, body: str, expected: str) -> None:
        errors = contract_errors(body)
        self.assertIn(expected, errors, errors)

    def test_baseline(self) -> None:
        self.assert_contract_passes(self.body)

    def test_copy_capacity_boundaries(self) -> None:
        capacity = source_string_path_capacity()
        self.assertGreater(capacity, 256)
        self.assertTrue(256 < capacity)
        self.assertTrue(capacity - 1 < capacity)
        self.assertFalse(capacity < capacity)

    def test_string256_regression_fails(self) -> None:
        self.assert_contract_fails(
            self.body.replace("string_path temp_file_name;", "string256 temp_file_name;", 1),
            "Register must declare temp_file_name as string_path",
        )

    def test_ignored_copy_result_regression_fails(self) -> None:
        self.assert_contract_fails(
            self.body.replace("const int copy_error = ", "", 1),
            "Register must retain xr_strcpy's result for temp_file_name",
        )

    def test_late_copy_check_regression_fails(self) -> None:
        guard_start, guard_end = copy_error_guard_span(self.body)
        guard = self.body[guard_start:guard_end]
        without_guard = self.body[:guard_start] + self.body[guard_end:]
        normalizer = "xr_fs_strlwr(temp_file_name);"
        late_at = without_guard.index(normalizer) + len(normalizer)
        mutated = (
            without_guard[:late_at]
            + guard
            + "\n    "
            + without_guard[late_at:]
        )
        self.assert_contract_fails(
            mutated,
            "Register must check xr_strcpy before normalizing or registering the path",
        )

    def test_silent_return_regression_fails(self) -> None:
        guard_start, guard_end = copy_error_guard_span(self.body)
        mutated = (
            self.body[:guard_start]
            + "if (copy_error != 0)\n        return nullptr;"
            + self.body[guard_end:]
        )
        self.assert_contract_fails(
            mutated,
            "Register must not silently return nullptr for a rejected path",
        )

    def test_missing_empty_check_regression_fails(self) -> None:
        self.assert_contract_fails(
            self.body.replace(
                'CHECK_OR_EXIT(name && *name, "Cannot register an empty filesystem path.");\n\n',
                "",
                1,
            ),
            "Register must reject an empty filesystem path",
        )

    def test_missing_xr_strdup_regression_fails(self) -> None:
        self.assert_contract_fails(
            self.body.replace("desc.name = xr_strdup(desc.name);", "desc.name = temp_file_name;", 1),
            "Register must retain xr_strdup before inserting a new file entry",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
