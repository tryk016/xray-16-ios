#!/usr/bin/env python3
"""Static contract for the deliberately tiny iOS F5/F9 diagnostic surface."""

from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/xrEngine/xr_input.cpp"
CORE = ROOT / "src/xrCore/xrCore.cpp"
WRAPPER = ROOT / "misc/ios/input.sh"


class QuickLoadInputContractTests(unittest.TestCase):
    def test_only_canonical_f5_f9_are_mapped(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn('xr_strcmp(key, "f5") == 0', source)
        self.assertIn('mapped_key = SDL_SCANCODE_F5;', source)
        self.assertIn('xr_strcmp(key, "f9") == 0', source)
        self.assertIn('mapped_key = SDL_SCANCODE_F9;', source)
        self.assertIn("nonCanonicalFunctionKey", source)
        for rejected in ("SDL_SCANCODE_F1", "SDL_SCANCODE_F10", "SDL_SCANCODE_F12"):
            self.assertNotIn(rejected, source)

    def test_physical_wrapper_documents_only_the_two_keys(self) -> None:
        wrapper = WRAPPER.read_text(encoding="utf-8")
        self.assertIn("f5|f9", wrapper)
        self.assertNotIn("f1|f2", wrapper)

    def test_ios_username_fallback_is_post_sanitize_and_empty_only(self) -> None:
        core = CORE.read_text(encoding="utf-8")
        fallback = re.compile(
            r"#if defined\(XR_PLATFORM_APPLE_IOS\)\s*"
            r"if \(UserName\[0\] == '\\0'\)\s*"
            r'xr_strcpy\(UserName, sizeof\(UserName\), "Player"\);\s*'
            r"#endif"
        )
        match = fallback.search(core)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertLess(core.index("SanitizeString(UserName);"), match.start())
        self.assertLess(core.index("SanitizeString(CompName);"), match.start())
        self.assertNotIn("CompName", match.group(0))
        self.assertEqual(
            core.count('xr_strcpy(UserName, sizeof(UserName), "Player");'), 1,
        )


if __name__ == "__main__":
    unittest.main()
