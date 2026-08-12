#!/usr/bin/env python3
"""Mutation coverage for the iOS GL half-depth resource permutation."""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_FILES = (
    "src/Layers/xrRender_R2/r2.cpp",
    "src/Layers/xrRender_R2/r2_rendertarget.cpp",
    "src/Layers/xrRenderPC_GL/gl_rendertarget_phase_combine.cpp",
    "src/Layers/xrRenderPC_GL/rgl_shaders.cpp",
    "res/gamedata/shaders/gl/effects_water.s",
    "res/gamedata/shaders/gl/ssr.h",
)

OGL_HBAO = "OGL must keep HBAO unavailable"
OGL_HDAO = "OGL must keep HDAO unavailable"
SSAO_OPT = "SSAO optimized data must require enabled SSAO"
SSAO_HALF = "SSAO half data must require optimized data"
SSR_EMISSION = "SSR half-depth must have one resource-guarded shader-option emission"
HALF_ALLOCATION = "half-depth must have one optimized-data-guarded allocation"
HALF_DOWNSAMPLE = "half-depth must have one optimized-data-guarded downsample"
WATER_BINDING = "water normal pass must have one half-depth binding"
SSR_SAMPLE = "SSR half-depth branch must have one half-depth sample"


def _consume_quoted(text: str, start: int, output: list[str]) -> int:
    quote = text[start]
    output.append(quote)
    index = start + 1
    while index < len(text):
        character = text[index]
        output.append(character)
        index += 1
        if character == "\\" and index < len(text):
            output.append(text[index])
            index += 1
        elif character == quote:
            break
    return index


def _blank_comment(text: str, start: int, end: int, output: list[str]) -> None:
    output.extend("\n" if character == "\n" else " " for character in text[start:end])


def strip_c_comments(text: str) -> str:
    """Remove active C/C++/GLSL comments while preserving strings and lines."""
    output: list[str] = []
    index = 0
    while index < len(text):
        if text[index] in {'"', "'"}:
            index = _consume_quoted(text, index, output)
        elif text.startswith("//", index):
            end = text.find("\n", index)
            end = len(text) if end < 0 else end
            _blank_comment(text, index, end, output)
            index = end
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            end = len(text) if end < 0 else end + 2
            _blank_comment(text, index, end, output)
            index = end
        else:
            output.append(text[index])
            index += 1
    return "".join(output)


def _lua_long_bracket(text: str, start: int) -> tuple[int, str] | None:
    match = re.match(r"\[(=*)\[", text[start:])
    if match is None:
        return None
    return match.end(), "]" + match.group(1) + "]"


def strip_lua_comments(text: str) -> str:
    """Remove Lua line/long comments while preserving strings and long strings."""
    output: list[str] = []
    index = 0
    while index < len(text):
        if text[index] in {'"', "'"}:
            index = _consume_quoted(text, index, output)
            continue

        long_string = _lua_long_bracket(text, index)
        if long_string is not None:
            opener_length, closer = long_string
            end = text.find(closer, index + opener_length)
            end = len(text) if end < 0 else end + len(closer)
            output.append(text[index:end])
            index = end
            continue

        if text.startswith("--", index):
            long_comment = _lua_long_bracket(text, index + 2)
            if long_comment is not None:
                opener_length, closer = long_comment
                end = text.find(closer, index + 2 + opener_length)
                end = len(text) if end < 0 else end + len(closer)
            else:
                end = text.find("\n", index)
                end = len(text) if end < 0 else end
            _blank_comment(text, index, end, output)
            index = end
            continue

        output.append(text[index])
        index += 1
    return "".join(output)


def source(root: Path, relative_path: str) -> str:
    text = (root / relative_path).read_text(encoding="utf-8")
    if relative_path.endswith(".s"):
        return strip_lua_comments(text)
    return strip_c_comments(text)


def count(pattern: str, text: str) -> int:
    return len(re.findall(pattern, text, re.MULTILINE | re.DOTALL))


def require_count(errors: list[str], pattern: str, text: str, expected: int, label: str) -> None:
    actual = count(pattern, text)
    if actual != expected:
        errors.append(f"{label}: expected {expected} active occurrence(s), found {actual}")


def braced_body(text: str, header_pattern: str) -> str | None:
    header = re.search(header_pattern, text, re.MULTILINE | re.DOTALL)
    if header is None:
        return None
    opening = text.find("{", header.start(), header.end())
    if opening < 0:
        return None

    depth = 1
    index = opening + 1
    while index < len(text):
        if text[index] in {'"', "'"}:
            sink: list[str] = []
            index = _consume_quoted(text, index, sink)
            continue
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[opening + 1:index]
        index += 1
    return None


def preprocessor_branches(text: str, macro: str) -> tuple[str, str] | None:
    directive = re.compile(r"^[ \t]*#\s*(if|ifdef|ifndef|elif|else|endif)\b([^\n]*)", re.MULTILINE)
    start = re.search(rf"^[ \t]*#\s*ifndef\s+{re.escape(macro)}\b[^\n]*", text, re.MULTILINE)
    if start is None:
        return None

    depth = 1
    split: re.Match[str] | None = None
    for match in directive.finditer(text, start.end()):
        kind = match.group(1)
        if kind in {"if", "ifdef", "ifndef"}:
            depth += 1
        elif kind == "endif":
            depth -= 1
            if depth == 0:
                if split is None:
                    return None
                return text[start.end():split.start()], text[split.end():match.start()]
        elif kind == "else" and depth == 1:
            split = match
    return None


def validate(root: Path) -> list[str]:
    """Return active-source contract errors; no hand-maintained resource manifest."""
    errors: list[str] = []
    r2 = source(root, "src/Layers/xrRender_R2/r2.cpp")
    target = source(root, "src/Layers/xrRender_R2/r2_rendertarget.cpp")
    combine = source(root, "src/Layers/xrRenderPC_GL/gl_rendertarget_phase_combine.cpp")
    shaders = source(root, "src/Layers/xrRenderPC_GL/rgl_shaders.cpp")
    water = source(root, "res/gamedata/shaders/gl/effects_water.s")
    ssr = source(root, "res/gamedata/shaders/gl/ssr.h")

    ogl_match = re.search(
        r"#if\s+defined\(USE_DX11\)\s*"
        r"o\.ssao_hdao\s*=.*?"
        r"#elif\s+defined\(USE_OGL\)(?P<body>.*?)"
        r"#else\s*#\s*error\s+No graphics API selected or enabled!\s*#endif",
        r2,
        re.MULTILINE | re.DOTALL,
    )
    if ogl_match is None:
        errors.extend((OGL_HBAO, OGL_HDAO))
    else:
        ogl = ogl_match.group("body")
        require_count(errors, r"\bo\.ssao_hbao\s*=", ogl, 1, OGL_HBAO)
        require_count(errors, r"\bo\.ssao_hbao\s*=\s*false\s*;", ogl, 1, OGL_HBAO)
        require_count(errors, r"\bo\.ssao_hdao\s*=", ogl, 1, OGL_HDAO)
        require_count(errors, r"\bo\.ssao_hdao\s*=\s*false\s*;", ogl, 1, OGL_HDAO)

    require_count(errors, r"\bo\.ssao_opt_data\s*=", r2, 1, SSAO_OPT)
    require_count(
        errors,
        r"\bo\.ssao_opt_data\s*=\s*ps_r2_ls_flags_ext\.test\(R2FLAGEXT_SSAO_OPT_DATA\)\s*"
        r"&&\s*\(ps_r_ssao\s*!=\s*0\)\s*;",
        r2,
        1,
        SSAO_OPT,
    )
    require_count(errors, r"\bo\.ssao_half_data\s*=", r2, 1, SSAO_HALF)
    require_count(
        errors,
        r"\bo\.ssao_half_data\s*=\s*ps_r2_ls_flags_ext\.test\(R2FLAGEXT_SSAO_HALF_DATA\)\s*"
        r"&&\s*o\.ssao_opt_data\s*&&\s*\(ps_r_ssao\s*!=\s*0\)\s*;",
        r2,
        1,
        SSAO_HALF,
    )

    emission_pattern = (
        r"\b(?:appendShaderOption|options\.add)\s*\([^;]*"
        r"\"SSR_HALF_DEPTH\"[^;]*\)\s*;"
    )
    require_count(errors, emission_pattern, shaders, 1, SSR_EMISSION)
    water_options = braced_body(
        shaders,
        r"if\s*\(\s*RImplementation\.o\.advancedpp\s*&&\s*ps_r_water_reflection\s*\)\s*\{",
    )
    guarded_emission = (
        r"const\s+bool\s+ssrHalfDepth\s*=\s*"
        r"ps_r2_ls_flags_ext\.test\(R3FLAGEXT_SSR_HALF_DEPTH\)\s*"
        r"&&\s*o\.ssao_opt_data\s*;\s*"
        r"appendShaderOption\(ssrHalfDepth,\s*\"SSR_HALF_DEPTH\",\s*\"1\"\)\s*;"
    )
    if water_options is None:
        errors.append(f"{SSR_EMISSION}: water reflection option block not found")
    else:
        require_count(errors, emission_pattern, water_options, 1, SSR_EMISSION)
        require_count(errors, guarded_emission, water_options, 1, SSR_EMISSION)

    allocation_call = r"\brt_half_depth\s*\.\s*create\s*\("
    allocation_pattern = allocation_call + r"\s*r2_RT_half_depth\s*,"
    require_count(errors, allocation_call, target, 1, HALF_ALLOCATION)
    require_count(errors, allocation_pattern, target, 1, HALF_ALLOCATION)
    allocation_block = braced_body(target, r"if\s*\(\s*options\.ssao_opt_data\s*\)\s*\{")
    if allocation_block is None:
        errors.append(f"{HALF_ALLOCATION}: optimized-data block not found")
    else:
        require_count(errors, allocation_call, allocation_block, 1, HALF_ALLOCATION)
        require_count(errors, allocation_pattern, allocation_block, 1, HALF_ALLOCATION)

    downsample_pattern = r"\bphase_downsamp\s*\(\s*\)\s*;"
    require_count(errors, downsample_pattern, combine, 1, HALF_DOWNSAMPLE)
    downsample_block = braced_body(
        combine,
        r"if\s*\(\s*RImplementation\.o\.ssao_opt_data\s*\)\s*\{",
    )
    if downsample_block is None:
        errors.append(f"{HALF_DOWNSAMPLE}: optimized-data block not found")
    else:
        require_count(errors, downsample_pattern, downsample_block, 1, HALF_DOWNSAMPLE)

    sampler_pattern = r"\bshader\s*:\s*sampler\s*\(\s*\"s_half_depth\"\s*\)"
    binding_pattern = (
        sampler_pattern + r"\s*:\s*texture\s*\(\s*\"\$user\$half_depth\"\s*\)"
    )
    require_count(errors, sampler_pattern, water, 1, WATER_BINDING)
    require_count(errors, r"\"\$user\$half_depth\"", water, 1, WATER_BINDING)
    require_count(errors, binding_pattern, water, 1, WATER_BINDING)
    normal = re.search(
        r"\bfunction\s+normal\s*\([^)]*\)(?P<body>.*?)\bend\s*"
        r"(?=\bfunction\s+l_special\b)",
        water,
        re.MULTILINE | re.DOTALL,
    )
    if normal is None:
        errors.append(f"{WATER_BINDING}: normal function not found")
    else:
        require_count(errors, sampler_pattern, normal.group("body"), 1, WATER_BINDING)
        require_count(errors, binding_pattern, normal.group("body"), 1, WATER_BINDING)

    half_sample_pattern = r"\btex2D\s*\(\s*s_half_depth\s*,"
    require_count(errors, half_sample_pattern, ssr, 1, SSR_SAMPLE)
    branches = preprocessor_branches(ssr, "SSR_HALF_DEPTH")
    if branches is None:
        errors.append(f"{SSR_SAMPLE}: full/half preprocessor branches not found")
    else:
        full_depth, half_depth = branches
        require_count(errors, r"\bs_half_depth\b", full_depth, 0, SSR_SAMPLE)
        require_count(errors, r"\b(?:tex2D|texelFetch)\s*\(\s*s_position\s*,", full_depth, 2, SSR_SAMPLE)
        require_count(errors, half_sample_pattern, half_depth, 1, SSR_SAMPLE)
        require_count(errors, r"\bs_position\b", half_depth, 0, SSR_SAMPLE)

    return errors


class ShaderResourceContractTest(unittest.TestCase):
    def run_contract(self, mutate=None) -> list[str]:
        with tempfile.TemporaryDirectory(prefix="openxray-shader-resource-") as temporary:
            root = Path(temporary)
            for relative_path in SOURCE_FILES:
                destination = root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(REPO_ROOT / relative_path, destination)
            if mutate is not None:
                mutate(root)
            return validate(root)

    def assert_passes(self, mutate=None) -> None:
        errors = self.run_contract(mutate)
        self.assertEqual(errors, [], "\n".join(errors))

    def assert_fails(self, mutate, expected: str) -> None:
        errors = self.run_contract(mutate)
        self.assertTrue(any(expected in error for error in errors), "\n".join(errors))

    @staticmethod
    def replace(root: Path, relative_path: str, before: str, after: str) -> None:
        path = root / relative_path
        text = path.read_text(encoding="utf-8")
        if before not in text:
            raise AssertionError(f"missing mutation source: {relative_path}: {before}")
        path.write_text(text.replace(before, after, 1), encoding="utf-8")

    def test_baseline(self) -> None:
        self.assert_passes()

    def test_ogl_hbao_enable_fails(self) -> None:
        self.assert_fails(
            lambda root: self.replace(root, "src/Layers/xrRender_R2/r2.cpp", "o.ssao_hbao = false;", "o.ssao_hbao = true;"),
            OGL_HBAO,
        )

    def test_ogl_hdao_enable_fails(self) -> None:
        self.assert_fails(
            lambda root: self.replace(root, "src/Layers/xrRender_R2/r2.cpp", "o.ssao_hdao = false;", "o.ssao_hdao = true;"),
            OGL_HDAO,
        )

    def test_optimized_data_without_ssao_fails(self) -> None:
        self.assert_fails(
            lambda root: self.replace(
                root,
                "src/Layers/xrRender_R2/r2.cpp",
                "o.ssao_opt_data = ps_r2_ls_flags_ext.test(R2FLAGEXT_SSAO_OPT_DATA) && (ps_r_ssao != 0);",
                "o.ssao_opt_data = ps_r2_ls_flags_ext.test(R2FLAGEXT_SSAO_OPT_DATA);",
            ),
            SSAO_OPT,
        )

    def test_half_data_without_optimized_data_fails(self) -> None:
        self.assert_fails(
            lambda root: self.replace(root, "src/Layers/xrRender_R2/r2.cpp", " && o.ssao_opt_data", ""),
            SSAO_HALF,
        )

    def test_ssr_half_emission_without_resource_fails(self) -> None:
        self.assert_fails(
            lambda root: self.replace(root, "src/Layers/xrRenderPC_GL/rgl_shaders.cpp", " && o.ssao_opt_data", ""),
            SSR_EMISSION,
        )

    def test_additional_unconditional_ssr_emission_fails(self) -> None:
        self.assert_fails(
            lambda root: self.replace(
                root,
                "src/Layers/xrRenderPC_GL/rgl_shaders.cpp",
                "        const bool ssrHalfDepth =\n",
                '        appendShaderOption(true, "SSR_HALF_DEPTH", "1");\n        const bool ssrHalfDepth =\n',
            ),
            SSR_EMISSION,
        )

    def test_half_depth_allocation_guard_fails(self) -> None:
        self.assert_fails(
            lambda root: self.replace(root, "src/Layers/xrRender_R2/r2_rendertarget.cpp", "if (options.ssao_opt_data)", "if (true)"),
            HALF_ALLOCATION,
        )

    def test_half_depth_downsample_guard_fails(self) -> None:
        self.assert_fails(
            lambda root: self.replace(root, "src/Layers/xrRenderPC_GL/gl_rendertarget_phase_combine.cpp", "if (RImplementation.o.ssao_opt_data)", "if (true)"),
            HALF_DOWNSAMPLE,
        )

    def test_additional_unconditional_downsample_fails(self) -> None:
        self.assert_fails(
            lambda root: self.replace(
                root,
                "src/Layers/xrRenderPC_GL/gl_rendertarget_phase_combine.cpp",
                "    if (RImplementation.o.ssao_hdao)\n",
                "    phase_downsamp();\n\n    if (RImplementation.o.ssao_hdao)\n",
            ),
            HALF_DOWNSAMPLE,
        )

    def test_water_half_depth_binding_fails(self) -> None:
        self.assert_fails(
            lambda root: self.replace(root, "res/gamedata/shaders/gl/effects_water.s", '"$user$half_depth"', '"$user$position"'),
            WATER_BINDING,
        )

    def test_commented_water_half_depth_binding_fails(self) -> None:
        self.assert_fails(
            lambda root: self.replace(
                root,
                "res/gamedata/shaders/gl/effects_water.s",
                '\tshader:sampler        ("s_half_depth") :texture  ("$user$half_depth")',
                '\t-- shader:sampler        ("s_half_depth") :texture  ("$user$half_depth")',
            ),
            WATER_BINDING,
        )

    def test_ssr_half_sample_branch_fails(self) -> None:
        self.assert_fails(
            lambda root: self.replace(root, "res/gamedata/shaders/gl/ssr.h", "s_half_depth", "s_position"),
            SSR_SAMPLE,
        )


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=0).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(ShaderResourceContractTest)
    )
    if result.wasSuccessful():
        print(f"Shader resource contract mutation tests: {result.testsRun}/{result.testsRun} PASS")
    raise SystemExit(0 if result.wasSuccessful() else 1)
