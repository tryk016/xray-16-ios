#!/usr/bin/env python3
"""Subprocess mutation coverage for the iOS numeric feature-macro contract."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "misc/ios/shadercheck/glsl_es_check.py"
SHADER_ROOT = REPO_ROOT / "res/gamedata/shaders/gl"


class NumericFeatureMacroContractTest(unittest.TestCase):
    def run_contract(self, mutate=None) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory(prefix="openxray-shader-contract-") as temporary:
            root = Path(temporary) / "gl"
            shutil.copytree(SHADER_ROOT, root)
            if mutate is not None:
                mutate(root)
            environment = os.environ.copy()
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            return subprocess.run(
                [sys.executable, str(CHECKER), "--shaders", str(root), "--macro-contract"],
                cwd=REPO_ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

    def assert_passes(self, mutate=None) -> None:
        result = self.run_contract(mutate)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Numeric feature-macro contract: PASS", result.stdout)

    def assert_fails(self, mutate, expected: str) -> None:
        result = self.run_contract(mutate)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Numeric feature-macro contract: FAIL", result.stdout)
        self.assertIn(expected, result.stdout)

    @staticmethod
    def append(path: Path, text: str) -> None:
        path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")

    def test_baseline(self) -> None:
        self.assert_passes()

    def test_active_ssao_presence_test_fails(self) -> None:
        self.assert_fails(
            lambda root: self.append(root / "ssao.ps", "\n#ifdef SSAO_QUALITY\n#endif\n"),
            "ssao.ps: #ifdef SSAO_QUALITY",
        )

    def test_ssr_water_defined_test_fails(self) -> None:
        self.assert_fails(
            lambda root: self.append(root / "water.ps", "\n#if defined(SSR_QUALITY)\n#endif\n"),
            "water.ps: #if defined(SSR_QUALITY)",
        )

    def test_msaa_presence_test_fails(self) -> None:
        self.assert_fails(
            lambda root: self.append(root / "mark_msaa_edges.ps", "\n#ifndef MSAA_SAMPLES\n#endif\n"),
            "mark_msaa_edges.ps: #ifndef MSAA_SAMPLES",
        )

    def test_sun_presence_test_fails(self) -> None:
        self.assert_fails(
            lambda root: self.append(root / "accum_sun_near.ps", "\n#ifdef SUN_QUALITY\n#endif\n"),
            "accum_sun_near.ps: #ifdef SUN_QUALITY",
        )

    def test_missing_fallback_fails(self) -> None:
        def mutate(root: Path) -> None:
            path = root / "common.h"
            source = path.read_text(encoding="utf-8")
            source, count = re.subn(
                r"^\s*#\s*define\s+SUN_QUALITY\s+0\s*$\n?",
                "",
                source,
                count=1,
                flags=re.MULTILINE,
            )
            self.assertEqual(count, 1)
            path.write_text(source, encoding="utf-8")

        self.assert_fails(mutate, "#define SUN_QUALITY 0")

    def test_wrong_fallback_fails(self) -> None:
        def mutate(root: Path) -> None:
            path = root / "common.h"
            source, count = re.subn(
                r"^(\s*#\s*define\s+SSR_QUALITY\s+)0\s*$",
                r"\g<1>1",
                path.read_text(encoding="utf-8"),
                count=1,
                flags=re.MULTILINE,
            )
            self.assertEqual(count, 1)
            path.write_text(source, encoding="utf-8")

        self.assert_fails(mutate, "#define SSR_QUALITY 0")

    def test_duplicate_fallback_fails(self) -> None:
        self.assert_fails(
            lambda root: self.append(root / "common.h", "\n#define MSAA_SAMPLES 0\n"),
            "#define MSAA_SAMPLES 0",
        )

    def test_function_like_fallback_fails(self) -> None:
        def mutate(root: Path) -> None:
            path = root / "common.h"
            source, count = re.subn(
                r"^(\s*#\s*define\s+MSAA_SAMPLES)\s+0\s*$",
                r"\g<1>(x) 0",
                path.read_text(encoding="utf-8"),
                count=1,
                flags=re.MULTILINE,
            )
            self.assertEqual(count, 1)
            path.write_text(source, encoding="utf-8")

        self.assert_fails(mutate, "#define MSAA_SAMPLES 0")

    def test_missing_fallback_guard_fails(self) -> None:
        def mutate(root: Path) -> None:
            path = root / "common.h"
            source, count = re.subn(
                r"^[ \t]*#[ \t]*ifndef[ \t]+SUN_QUALITY[ \t]*\n"
                r"(?P<define>^[ \t]*#[ \t]*define[ \t]+SUN_QUALITY[ \t]+0[ \t]*\n)"
                r"^[ \t]*#[ \t]*endif\b[^\n]*\n?",
                r"\g<define>",
                path.read_text(encoding="utf-8"),
                count=1,
                flags=re.MULTILINE,
            )
            self.assertEqual(count, 1)
            path.write_text(source, encoding="utf-8")

        self.assert_fails(mutate, "#ifndef SUN_QUALITY")

    def test_duplicate_fallback_guard_fails(self) -> None:
        def mutate(root: Path) -> None:
            path = root / "common.h"
            source = path.read_text(encoding="utf-8")
            fallback = (
                "#ifndef SSR_QUALITY\n"
                "#  define SSR_QUALITY 0\n"
                "#endif"
            )
            nested_duplicate = (
                "#ifndef SSR_QUALITY\n"
                "#ifndef SSR_QUALITY\n"
                "#  define SSR_QUALITY 0\n"
                "#endif\n"
                "#endif"
            )
            self.assertEqual(source.count(fallback), 1)
            source = source.replace(fallback, nested_duplicate, 1)
            path.write_text(source, encoding="utf-8")

        self.assert_fails(mutate, "#ifndef SSR_QUALITY")

    def test_unrelated_common_presence_test_fails(self) -> None:
        self.assert_fails(
            lambda root: self.append(
                root / "common.h",
                "\n#ifndef SSAO_QUALITY\nconst int forbidden_presence = 1;\n#endif\n",
            ),
            "common.h: #ifndef SSAO_QUALITY",
        )

    def test_comment_only_fake_directives_pass(self) -> None:
        self.assert_passes(
            lambda root: self.append(
                root / "ssao.ps",
                "\n// #ifdef SSAO_QUALITY\n"
                "/* #if defined(SSR_QUALITY) */\n"
                "// #undef MSAA_SAMPLES\n",
            )
        )

    def test_intentional_presence_macro_passes(self) -> None:
        self.assert_passes(
            lambda root: self.append(root / "water.ps", "\n#ifdef USE_MSAA\n#endif\n")
        )

    def test_nested_unrelated_common_block_preserves_fallback(self) -> None:
        def mutate(root: Path) -> None:
            path = root / "common.h"
            source = path.read_text(encoding="utf-8")
            fallback = (
                "#ifndef MSAA_SAMPLES\n"
                "#  define MSAA_SAMPLES 0\n"
                "#endif"
            )
            nested = (
                "#ifndef MSAA_SAMPLES\n"
                "#  define MSAA_SAMPLES 0\n"
                "#ifndef INTENTIONAL_PRESENCE_MACRO\n"
                "#endif\n"
                "#endif"
            )
            self.assertEqual(source.count(fallback), 1)
            path.write_text(source.replace(fallback, nested, 1), encoding="utf-8")

        self.assert_passes(mutate)

    def test_outer_if_zero_around_common_header_fails(self) -> None:
        def mutate(root: Path) -> None:
            path = root / "common.h"
            source = path.read_text(encoding="utf-8")
            path.write_text(f"#if 0\n{source}#endif\n", encoding="utf-8")

        self.assert_fails(mutate, "complete top-level")

    def test_common_header_guard_alternative_fails(self) -> None:
        def mutate(root: Path) -> None:
            path = root / "common.h"
            source = path.read_text(encoding="utf-8")
            prefix, marker, suffix = source.rpartition("#endif")
            self.assertEqual(marker, "#endif")
            path.write_text(
                prefix
                + "#else\nconst int invalid_header_alternate = 1;\n#endif"
                + suffix,
                encoding="utf-8",
            )

        self.assert_fails(mutate, "no alternate branch")

    def test_numeric_fallback_inside_outer_conditional_fails(self) -> None:
        def mutate(root: Path) -> None:
            path = root / "common.h"
            source = path.read_text(encoding="utf-8")
            fallback = (
                "#ifndef SSAO_OPT_DATA\n"
                "#  define SSAO_OPT_DATA 0\n"
                "#endif"
            )
            wrapped = (
                "#if INTENTIONAL_OUTER_CONDITION\n"
                "#ifndef SSAO_OPT_DATA\n"
                "#  define SSAO_OPT_DATA 0\n"
                "#endif\n"
                "#else\n"
                "#endif"
            )
            self.assertEqual(source.count(fallback), 1)
            path.write_text(source.replace(fallback, wrapped, 1), encoding="utf-8")

        self.assert_fails(mutate, "direct child of the top-level COMMON_H guard")

    def test_numeric_fallback_nested_under_another_fails(self) -> None:
        def mutate(root: Path) -> None:
            path = root / "common.h"
            source = path.read_text(encoding="utf-8")
            adjacent = (
                "#ifndef SUN_QUALITY\n"
                "#  define SUN_QUALITY 0\n"
                "#endif\n"
                "#ifndef SSR_QUALITY\n"
                "#  define SSR_QUALITY 0\n"
                "#endif"
            )
            nested = (
                "#ifndef SUN_QUALITY\n"
                "#  define SUN_QUALITY 0\n"
                "#ifndef SSR_QUALITY\n"
                "#  define SSR_QUALITY 0\n"
                "#endif\n"
                "#endif"
            )
            self.assertEqual(source.count(adjacent), 1)
            path.write_text(source.replace(adjacent, nested, 1), encoding="utf-8")

        self.assert_fails(mutate, "direct child of the top-level COMMON_H guard")

    def test_appended_water_numeric_undef_fails(self) -> None:
        self.assert_fails(
            lambda root: self.append(root / "water.ps", "\n#undef SUN_QUALITY\n"),
            "water.ps: #undef SUN_QUALITY",
        )

    def test_numeric_undef_inside_sun_fallback_fails(self) -> None:
        def mutate(root: Path) -> None:
            path = root / "common.h"
            source = path.read_text(encoding="utf-8")
            define = "#  define SUN_QUALITY 0"
            self.assertEqual(source.count(define), 1)
            path.write_text(
                source.replace(define, define + "\n#undef SUN_QUALITY", 1),
                encoding="utf-8",
            )

        self.assert_fails(mutate, "common.h: #undef SUN_QUALITY")

    def test_existing_numeric_undef_removal_fails(self) -> None:
        def mutate(root: Path) -> None:
            path = root / "combine_1.ps"
            source, count = re.subn(
                r"^[ \t]*#[ \t]*undef[ \t]+SSAO_QUALITY[ \t]*\n?",
                "",
                path.read_text(encoding="utf-8"),
                count=1,
                flags=re.MULTILINE,
            )
            self.assertEqual(count, 1)
            path.write_text(source, encoding="utf-8")

        self.assert_fails(mutate, "combine_1.ps: #undef SSAO_QUALITY")

    def test_existing_numeric_undef_change_fails(self) -> None:
        def mutate(root: Path) -> None:
            path = root / "combine_1.ps"
            source, count = re.subn(
                r"^([ \t]*#[ \t]*undef[ \t]+)SSAO_QUALITY([ \t]*)$",
                r"\g<1>SSR_QUALITY\g<2>",
                path.read_text(encoding="utf-8"),
                count=1,
                flags=re.MULTILINE,
            )
            self.assertEqual(count, 1)
            path.write_text(source, encoding="utf-8")

        self.assert_fails(mutate, "combine_1.ps: #undef SSR_QUALITY")

    def test_numeric_undef_inside_if_zero_fails(self) -> None:
        self.assert_fails(
            lambda root: self.append(
                root / "water.ps",
                "\n#if 0\n#undef MSAA_SAMPLES\n#endif\n",
            ),
            "water.ps: #undef MSAA_SAMPLES",
        )

    def test_line_spliced_active_numeric_undef_fails(self) -> None:
        self.assert_fails(
            lambda root: self.append(
                root / "common.h", "\n#undef SSR_\\\nQUALITY\n"
            ),
            "common.h: #undef SSR_QUALITY",
        )

    def test_line_comment_continuation_hides_presence_test(self) -> None:
        self.assert_passes(
            lambda root: self.append(
                root / "ssao.ps",
                "\n// continued comment \\\n#ifdef SSAO_QUALITY\n",
            )
        )

    def test_legacy_debt_addition_fails(self) -> None:
        self.assert_fails(
            lambda root: self.append(root / "ssao_hbao.ps", "\n#ifdef SSAO_QUALITY\n#endif\n"),
            "ssao_hbao.ps: #ifdef SSAO_QUALITY",
        )

    def test_legacy_debt_removal_fails(self) -> None:
        def mutate(root: Path) -> None:
            path = root / "ssao_hbao.ps"
            source, count = re.subn(
                r"^\s*#\s*ifndef\s+SSAO_QUALITY\s*$\n?",
                "",
                path.read_text(encoding="utf-8"),
                count=1,
                flags=re.MULTILINE,
            )
            self.assertEqual(count, 1)
            path.write_text(source, encoding="utf-8")

        self.assert_fails(mutate, "ssao_hbao.ps: #ifndef SSAO_QUALITY")

    def test_legacy_debt_change_fails(self) -> None:
        def mutate(root: Path) -> None:
            path = root / "ssao_hbao.ps"
            source, count = re.subn(
                r"^(\s*#\s*)ifndef(\s+SSAO_OPT_DATA\s*)$",
                r"\g<1>ifdef\g<2>",
                path.read_text(encoding="utf-8"),
                count=1,
                flags=re.MULTILINE,
            )
            self.assertEqual(count, 1)
            path.write_text(source, encoding="utf-8")

        self.assert_fails(mutate, "ssao_hbao.ps: #ifdef SSAO_OPT_DATA")

    def test_multiline_defined_test_fails(self) -> None:
        self.assert_fails(
            lambda root: self.append(
                root / "water.ps",
                "\n#if defined( \\\n    SSR_QUALITY \\\n)\n#endif\n",
            ),
            "water.ps: #if defined(SSR_QUALITY)",
        )


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=0).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(NumericFeatureMacroContractTest)
    )
    if result.wasSuccessful():
        print(f"Shader macro contract mutation tests: {result.testsRun}/{result.testsRun} PASS")
    sys.exit(0 if result.wasSuccessful() else 1)
