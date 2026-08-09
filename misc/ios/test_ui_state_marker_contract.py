#!/usr/bin/env python3
"""Mutation-backed host contract for the iOS rendered UI-state marker."""

from __future__ import annotations

from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
GAME_UI = REPO_ROOT / "src/xrGame/UIGameCustom.cpp"
GAME_UI_HEADER = REPO_ROOT / "src/xrGame/UIGameCustom.h"
MAIN_MENU = REPO_ROOT / "src/xrGame/MainMenu.cpp"
STATE_WORDS = {"world", "inventory", "pda_tasks", "pda_map", "other"}


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


def render_body(source: str) -> str:
    signature = re.search(r"void\s+CUIGameCustom::Render\s*\(", source)
    if signature is None:
        raise AssertionError("CUIGameCustom::Render signature not found")

    opening = source.find("{", signature.end())
    if opening < 0:
        raise AssertionError("CUIGameCustom::Render body not found")

    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1:index]
    raise AssertionError("CUIGameCustom::Render body is not balanced")


def marker_errors(source: str, header: str) -> list[str]:
    errors: list[str] = []
    try:
        renderer = render_body(source)
        reporter = function_body(source, "ios_report_rendered_ui_state")
        classifier = function_body(source, "ios_rendered_ui_state")
        names = function_body(source, "ios_ui_state_name")
    except AssertionError as error:
        return [str(error)]

    ios_guard = re.search(
        r"#if\s+defined\(XR_PLATFORM_APPLE_IOS\)(?P<body>.*?)#endif",
        source,
        re.DOTALL,
    )
    if ios_guard is None or "ios_report_rendered_ui_state" not in ios_guard.group("body"):
        errors.append("marker implementation must be iOS-only")

    render_call = "ios_report_rendered_ui_state(ActorMenu, PdaMenu, TopInputReceiver());"
    dialog_draw = "DoRenderDialogs();"
    if render_call not in renderer:
        errors.append("Render must report the rendered UI state")
    elif dialog_draw not in renderer or renderer.index(render_call) <= renderer.index(dialog_draw):
        errors.append("Render must report state after DoRenderDialogs")

    if not re.search(
        r'Msg\(\s*"\* iOS UI state v1 pid=%d seq=%llu frame=%u state=%s"', reporter
    ):
        errors.append("marker grammar must be the exact versioned pid/seq/frame/state format")
    if not re.search(r"static_cast<int>\(process_id\)", reporter):
        errors.append("marker must emit the current process PID")
    if "Device.dwFrame" not in reporter:
        errors.append("marker must emit Device.dwFrame")
    if "getpid()" not in reporter or not re.search(r"process_id\s*<=\s*0", reporter):
        errors.append("marker must obtain and require a positive current-process PID")

    enum_words = set(re.findall(r'case\s+IosUiState::\w+\s*:\s*return\s+"([a-z_]+)"', names))
    if enum_words != STATE_WORDS:
        errors.append("state vocabulary must be exactly world/inventory/pda_tasks/pda_map/other")

    if not re.search(
        r"if\s*\(\s*!top_input_receiver\s*\)\s*return\s+IosUiState::world\s*;.*?"
        r"if\s*\(\s*actor_menu\s*&&\s*actor_menu->IsShown\(\)",
        classifier,
        re.DOTALL,
    ):
        errors.append("missing top input receiver must report world before menu classification")
    if not re.search(
        r"actor_menu\s*&&\s*actor_menu->IsShown\(\).*?top_input_receiver\s*!=\s*actor_menu.*?"
        r"return\s+IosUiState::other.*?GetMenuMode\(\)\s*==\s*mmInventory.*?"
        r"IosUiState::inventory.*?IosUiState::other",
        classifier,
        re.DOTALL,
    ):
        errors.append("only top shown ActorMenu may distinguish mmInventory from every other mode")
    for section, state in (("eptTasks", "pda_tasks"), ("eptMap", "pda_map")):
        if not re.search(
            rf'std::strcmp\(active_section,\s*"{section}"\)\s*==\s*0\)\s*return\s+IosUiState::{state}',
            classifier,
        ):
            errors.append(f"top shown PDA must map exact engine section {section}")
    if not re.search(
        r"pda_menu\s*&&\s*pda_menu->IsShown\(.*?top_input_receiver\s*!=\s*pda_menu.*?"
        r"return\s+IosUiState::other.*?std::strcmp\(active_section,\s*\"eptTasks\"\).*?"
        r"std::strcmp\(active_section,\s*\"eptMap\"\)",
        classifier,
        re.DOTALL,
    ):
        errors.append("shown PDA must be top input receiver before section classification")
    if not re.search(
        r"pda_menu\s*&&\s*pda_menu->IsShown\(.*?return\s+IosUiState::other;\s*}\s*return\s+IosUiState::other;",
        classifier,
        re.DOTALL,
    ):
        errors.append("non-menu top dialogs must report other after exact menu classification")

    disabled = re.search(
        r"if\s*\(\s*psIOSAutoInput\s*!=\s*1\s*\)\s*\{(?P<body>.*?)\}", reporter, re.DOTALL
    )
    if disabled is None or "has_last_state = false;" not in disabled.group("body"):
        errors.append("disabled ios_autoinput must reset the last observed state")
    if disabled is None or not re.search(r"has_last_state\s*=\s*false;\s*return;", disabled.group("body")):
        errors.append("disabled ios_autoinput must not leak a marker")
    if disabled is not None and re.search(r"\bsequence\s*=", disabled.group("body")):
        errors.append("disabling ios_autoinput must preserve the per-process sequence")
    if not re.search(r"if\s*\(\s*!has_last_state\s*\|\|\s*state\s*!=\s*last_state\s*\)", reporter):
        errors.append("marker must emit only on first observation or semantic state change")
    if not re.search(r"static\s+u64\s+sequence\s*=\s*0\s*;", reporter) or not re.search(
        r"\+\+sequence\s*;.*?Msg\(", reporter, re.DOTALL
    ):
        errors.append("marker must start and increment the positive sequence exactly when it emits")
    if not re.search(r"last_state\s*=\s*state;\s*has_last_state\s*=\s*true;", reporter):
        errors.append("marker must remember the emitted semantic state")

    if re.search(r"IosUiState|ios_report_rendered_ui_state|ios_rendered_ui_state|sequence", header):
        errors.append("marker state must remain private to UIGameCustom.cpp")
    return errors


def main_menu_marker_errors(source: str) -> list[str]:
    errors: list[str] = []
    try:
        reporter = function_body(source, "ios_report_rendered_main_menu_frame")
        ordinary_render = function_body(source, "CMainMenu::OnRender")
        pp_render = function_body(source, "CMainMenu::OnRenderPPUI_main")
    except AssertionError as error:
        return [str(error)]

    ios_sections = re.findall(
        r"#if\s+defined\(XR_PLATFORM_APPLE_IOS\)(?P<body>.*?)#endif",
        source,
        re.DOTALL,
    )
    if not any("ios_report_rendered_main_menu_frame" in section for section in ios_sections):
        errors.append("main-menu marker implementation must be iOS-only")
    if not any("#include <unistd.h>" in section for section in ios_sections):
        errors.append("main-menu marker process API include must be iOS-only")

    marker_call = "ios_report_rendered_main_menu_frame();"
    for label, render in (("ordinary", ordinary_render), ("postprocess", pp_render)):
        if marker_call not in render:
            errors.append(f"{label} menu render path must report a rendered frame")
            continue
        dialogs = render.find("DoRenderDialogs();")
        fonts = render.find("UI().RenderFont();")
        marker = render.find(marker_call)
        if dialogs < 0 or fonts < 0 or not dialogs < fonts < marker:
            errors.append(f"{label} menu marker must follow dialogs and font rendering")

    if source.count(marker_call) != 2:
        errors.append("main-menu marker must cover exactly the two authored render paths")
    if not re.search(
        r'Msg\(\s*"\* iOS main menu frame v1 pid=%d frame=%u"', reporter
    ):
        errors.append("main-menu marker grammar must be the exact versioned pid/frame format")
    if "getpid()" not in reporter or not re.search(r"process_id\s*<=\s*0", reporter):
        errors.append("main-menu marker must require a positive current-process PID")
    if "static_cast<int>(process_id)" not in reporter or "Device.dwFrame" not in reporter:
        errors.append("main-menu marker must emit its current PID and Device frame")
    if not re.search(r"static\s+bool\s+reported\s*=\s*false\s*;", reporter) or not re.search(
        r"if\s*\(\s*reported\s*\)\s*return\s*;", reporter
    ):
        errors.append("main-menu marker must be process-local and one-shot")
    message = reporter.find('Msg("* iOS main menu frame v1')
    remembered = reporter.find("reported = true;")
    if message < 0 or remembered <= message:
        errors.append("main-menu marker must become one-shot only after emission")
    return errors


class UiStateMarkerContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = GAME_UI.read_text(encoding="utf-8")
        cls.header = GAME_UI_HEADER.read_text(encoding="utf-8")

    def assert_contract_passes(self, source: str) -> None:
        self.assertEqual(marker_errors(source, self.header), [])

    def assert_contract_fails(self, source: str, expected: str) -> None:
        errors = marker_errors(source, self.header)
        self.assertIn(expected, errors, errors)

    def test_baseline(self) -> None:
        self.assert_contract_passes(self.source)

    def test_report_before_dialog_render_fails(self) -> None:
        marker = "ios_report_rendered_ui_state(ActorMenu, PdaMenu, TopInputReceiver());"
        mutated = self.source.replace(
            "    DoRenderDialogs();\n#if defined(XR_PLATFORM_APPLE_IOS)\n    " + marker,
            "#if defined(XR_PLATFORM_APPLE_IOS)\n    " + marker + "\n#endif\n    DoRenderDialogs();",
            1,
        )
        self.assert_contract_fails(mutated, "Render must report state after DoRenderDialogs")

    def test_unversioned_or_incomplete_grammar_fails(self) -> None:
        self.assert_contract_fails(
            self.source.replace("* iOS UI state v1", "* iOS UI state", 1),
            "marker grammar must be the exact versioned pid/seq/frame/state format",
        )

    def test_missing_autoinput_guard_fails(self) -> None:
        self.assert_contract_fails(
            self.source.replace("if (psIOSAutoInput != 1)", "if (false)", 1),
            "disabled ios_autoinput must reset the last observed state",
        )

    def test_actor_mode_collapse_fails(self) -> None:
        self.assert_contract_fails(
            self.source.replace(
                "return actor_menu->GetMenuMode() == mmInventory ? IosUiState::inventory : IosUiState::other;",
                "return IosUiState::inventory;",
                1,
            ),
            "only top shown ActorMenu may distinguish mmInventory from every other mode",
        )

    def test_pda_section_broadening_fails(self) -> None:
        self.assert_contract_fails(
            self.source.replace(
                'std::strcmp(active_section, "eptTasks") == 0',
                'std::strncmp(active_section, "eptTasks", 3) == 0',
                1,
            ),
            "top shown PDA must map exact engine section eptTasks",
        )

    def test_previous_pda_log_names_as_engine_ids_fail(self) -> None:
        self.assert_contract_fails(
            self.source.replace('"eptTasks"', '"pda_tasks"', 1),
            "top shown PDA must map exact engine section eptTasks",
        )
        self.assert_contract_fails(
            self.source.replace('"eptMap"', '"pda_map"', 1),
            "top shown PDA must map exact engine section eptMap",
        )

    def test_missing_actor_top_receiver_comparison_fails(self) -> None:
        self.assert_contract_fails(
            self.source.replace(
                "if (top_input_receiver != actor_menu)\n            return IosUiState::other;\n",
                "",
                1,
            ),
            "only top shown ActorMenu may distinguish mmInventory from every other mode",
        )

    def test_missing_pda_top_receiver_comparison_fails(self) -> None:
        self.assert_contract_fails(
            self.source.replace(
                "if (top_input_receiver != pda_menu)\n            return IosUiState::other;\n\n",
                "",
                1,
            ),
            "shown PDA must be top input receiver before section classification",
        )

    def test_missing_top_receiver_world_fallback_fails(self) -> None:
        self.assert_contract_fails(
            self.source.replace(
                "if (!top_input_receiver)\n        return IosUiState::world;",
                "if (!top_input_receiver)\n        return IosUiState::other;",
                1,
            ),
            "missing top input receiver must report world before menu classification",
        )

    def test_missing_other_dialog_fallback_fails(self) -> None:
        before, replaced, after = self.source.rpartition("return IosUiState::other;")
        self.assertTrue(replaced, "classifier must have a non-menu other fallback")
        self.assert_contract_fails(
            before + "return IosUiState::world;" + after,
            "non-menu top dialogs must report other after exact menu classification",
        )

    def test_per_frame_emission_regression_fails(self) -> None:
        self.assert_contract_fails(
            self.source.replace(
                "if (!has_last_state || state != last_state)",
                "if (true)",
                1,
            ),
            "marker must emit only on first observation or semantic state change",
        )

    def test_sequence_reset_when_reenabled_fails(self) -> None:
        self.assert_contract_fails(
            self.source.replace(
                "has_last_state = false;\n        return;",
                "has_last_state = false;\n        sequence = 0;\n        return;",
                1,
            ),
            "disabling ios_autoinput must preserve the per-process sequence",
        )

    def test_nonpositive_sequence_regression_fails(self) -> None:
        self.assert_contract_fails(
            self.source.replace("++sequence;", "sequence += 0;", 1),
            "marker must start and increment the positive sequence exactly when it emits",
        )

    def test_header_state_regression_fails(self) -> None:
        errors = marker_errors(self.source, self.header + "\nIosUiState leaked_state;\n")
        self.assertIn("marker state must remain private to UIGameCustom.cpp", errors, errors)


class MainMenuFrameMarkerContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = MAIN_MENU.read_text(encoding="utf-8")

    def assert_contract_fails(self, source: str, expected: str) -> None:
        errors = main_menu_marker_errors(source)
        self.assertIn(expected, errors, errors)

    def test_baseline(self) -> None:
        self.assertEqual(main_menu_marker_errors(self.source), [])

    def test_unversioned_grammar_fails(self) -> None:
        self.assert_contract_fails(
            self.source.replace("* iOS main menu frame v1", "* iOS main menu frame", 1),
            "main-menu marker grammar must be the exact versioned pid/frame format",
        )

    def test_missing_positive_pid_guard_fails(self) -> None:
        self.assert_contract_fails(
            self.source.replace("if (process_id <= 0)", "if (false)", 1),
            "main-menu marker must require a positive current-process PID",
        )

    def test_per_frame_regression_fails(self) -> None:
        self.assert_contract_fails(
            self.source.replace("if (reported)", "if (false)", 1),
            "main-menu marker must be process-local and one-shot",
        )

    def test_reported_before_emission_fails(self) -> None:
        message = '    Msg("* iOS main menu frame v1 pid=%d frame=%u", static_cast<int>(process_id), Device.dwFrame);\n'
        mutated = self.source.replace(
            message + "    reported = true;\n",
            "    reported = true;\n" + message,
            1,
        )
        self.assert_contract_fails(
            mutated,
            "main-menu marker must become one-shot only after emission",
        )

    def test_ordinary_marker_before_font_render_fails(self) -> None:
        ordered = (
            "        UI().RenderFont();\n"
            "#if defined(XR_PLATFORM_APPLE_IOS)\n"
            "        ios_report_rendered_main_menu_frame();\n"
            "#endif\n"
        )
        mutated = self.source.replace(
            ordered,
            "#if defined(XR_PLATFORM_APPLE_IOS)\n"
            "        ios_report_rendered_main_menu_frame();\n"
            "#endif\n"
            "        UI().RenderFont();\n",
            1,
        )
        self.assert_contract_fails(
            mutated,
            "ordinary menu marker must follow dialogs and font rendering",
        )

    def test_missing_postprocess_path_fails(self) -> None:
        before, marker, after = self.source.rpartition("        ios_report_rendered_main_menu_frame();")
        self.assertTrue(marker)
        self.assert_contract_fails(
            before + after,
            "postprocess menu render path must report a rendered frame",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
