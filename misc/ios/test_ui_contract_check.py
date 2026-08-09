#!/usr/bin/env python3
"""Regression fixtures for misc/ios/ui_contract_check.py."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "misc/ios/ui_contract_check.py"
FIXTURE_FILES = (
    "res/gamedata/configs/ui/ui_mm_opt_ios_16.xml",
    "res/gamedata/scripts/ui_mm_opt_video.script",
    "res/gamedata/scripts/ui_mm_opt_video_adv.script",
    "res/gamedata/scripts/ui_mm_opt_main.script",
    "src/xrGame/ui/UIMMShniaga.cpp",
    "src/xrEngine/ios/ios_graphics_profile.cpp",
    "src/xrEngine/xr_ioc_cmd.cpp",
    "src/xrEngine/device.cpp",
    "src/xrGame/ui/UITaskWnd.cpp",
    "src/xrGame/ui/UIFactionWarWnd.cpp",
    "src/xrGame/ui/UIDragDropListEx.cpp",
    "src/xrGame/UIDialogHolder.cpp",
    "src/xrGame/ui/ios_ui_focus_geometry_policy.h",
    "src/xrUICore/ScrollView/UIScrollView.h",
    "src/xrUICore/ScrollView/UIScrollView.cpp",
)


class UIContractCheckTests(unittest.TestCase):
    def make_fixture(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="ui-contract-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        for relative in FIXTURE_FILES:
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO_ROOT / relative, destination)
        return root

    def run_checker(self, root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            (sys.executable, str(CHECKER), "--source-root", str(root)),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def assert_fails(self, root: Path, message: str) -> None:
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"FAIL: {message}", result.stderr)

    @staticmethod
    def replace_once(path: Path, old: str, new: str) -> None:
        source = path.read_text(encoding="utf-8")
        if source.count(old) != 1:
            raise AssertionError(f"fixture replacement was not unique in {path}: {old!r}")
        path.write_text(source.replace(old, new), encoding="utf-8")

    def test_positive_fixture_passes(self) -> None:
        result = self.run_checker(self.make_fixture())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "iOS UI contract: PASS\n")
        self.assertEqual(result.stderr, "")

    def test_main_menu_filter_in_comment_fails(self) -> None:
        root = self.make_fixture()
        path = root / "src/xrGame/ui/UIMMShniaga.cpp"
        self.replace_once(
            path,
            '''        if (xr_strcmp(button_name, "btn_net_game") == 0 ||
            xr_strcmp(button_name, "btn_multiplayer") == 0)
            continue;''',
            '''        /* if (xr_strcmp(button_name, "btn_net_game") == 0 ||
            xr_strcmp(button_name, "btn_multiplayer") == 0)
            continue; */''',
        )
        self.assert_fails(root, "iOS main-menu construction does not filter every multiplayer entry")

    def test_main_menu_filter_outside_create_list_fails(self) -> None:
        root = self.make_fixture()
        path = root / "src/xrGame/ui/UIMMShniaga.cpp"
        self.replace_once(
            path,
            '''        if (xr_strcmp(button_name, "btn_net_game") == 0 ||
            xr_strcmp(button_name, "btn_multiplayer") == 0)
            continue;''',
            "",
        )
        with path.open("a", encoding="utf-8") as source:
            source.write('''
#if defined(XR_PLATFORM_APPLE_IOS)
if (xr_strcmp(button_name, "btn_net_game") == 0 ||
    xr_strcmp(button_name, "btn_multiplayer") == 0)
    continue;
#endif
''')
        self.assert_fails(root, "iOS main-menu construction does not filter every multiplayer entry")

    def test_main_menu_filter_after_allocation_fails(self) -> None:
        root = self.make_fixture()
        path = root / "src/xrGame/ui/UIMMShniaga.cpp"
        filter_block = '''#if defined(XR_PLATFORM_APPLE_IOS)
        // Multiplayer is not part of the iOS product. Filter the source XML at
        // list construction time because retail data may provide a different
        // ui_mm_main.xml than the fallback files shipped in this repository.
        if (xr_strcmp(button_name, "btn_net_game") == 0 ||
            xr_strcmp(button_name, "btn_multiplayer") == 0)
            continue;
#endif

        '''
        self.replace_once(path, filter_block + 'auto* st = xr_new<CUIStatic>("Button");',
                          'auto* st = xr_new<CUIStatic>("Button");\n\n        ' + filter_block)
        self.assert_fails(root, "multiplayer filtering must happen before the menu button is allocated")

    def test_main_menu_filter_in_nested_literal_inactive_branch_fails(self) -> None:
        root = self.make_fixture()
        path = root / "src/xrGame/ui/UIMMShniaga.cpp"
        filter_block = '''        if (xr_strcmp(button_name, "btn_net_game") == 0 ||
            xr_strcmp(button_name, "btn_multiplayer") == 0)
            continue;'''
        self.replace_once(
            path,
            filter_block,
            "#if 1\n#if 0\n" + filter_block + "\n#endif\n#endif",
        )
        self.assert_fails(root, "iOS main-menu construction does not filter every multiplayer entry")

    def test_main_menu_filter_only_in_outer_ios_else_fails(self) -> None:
        root = self.make_fixture()
        path = root / "src/xrGame/ui/UIMMShniaga.cpp"
        filter_block = '''        if (xr_strcmp(button_name, "btn_net_game") == 0 ||
            xr_strcmp(button_name, "btn_multiplayer") == 0)
            continue;'''
        self.replace_once(path, filter_block, "#else\n" + filter_block)
        self.assert_fails(root, "iOS main-menu construction does not filter every multiplayer entry")

    def test_profile_runtime_knob_in_comment_fails(self) -> None:
        root = self.make_fixture()
        path = root / "src/xrEngine/ios/ios_graphics_profile.cpp"
        self.replace_once(path, 'ExecuteFloat("rs_vis_distance", values.visibility);',
                          '/* ExecuteFloat("rs_vis_distance", values.visibility); */')
        self.assert_fails(root, "profile controller is missing a runtime-safe quality control")

    def test_native_resolution_dimensions_in_lua_comment_fail(self) -> None:
        root = self.make_fixture()
        path = root / "res/gamedata/scripts/ui_mm_opt_video.script"
        self.replace_once(
            path,
            'resolution:TextControl():SetText(string.format("%dx%d", device().width, device().height))',
            '-- resolution must use device().width and device().height',
        )
        self.assert_fails(root, "resolution script does not use the native render dimensions")

    def test_video_profile_and_desktop_hiding_in_lua_comments_fail(self) -> None:
        root = self.make_fixture()
        path = root / "res/gamedata/scripts/ui_mm_opt_video.script"
        self.replace_once(
            path,
            'xml:InitComboBox\t\t\t\t("tab_video:list_ios_profile",\t\tself)',
            '-- xml:InitComboBox("tab_video:list_ios_profile", self)',
        )
        self.replace_once(
            path,
            'cap_window_mode:Show\t\t\t(false)',
            '-- cap_window_mode:Show(false)',
        )
        self.assert_fails(root, "iOS Video script does not expose the profile and hide desktop-only controls")

    def test_graphics_profile_group_in_lua_comments_fails(self) -> None:
        root = self.make_fixture()
        path = root / "res/gamedata/scripts/ui_mm_opt_main.script"
        source = path.read_text(encoding="utf-8")
        self.assertEqual(source.count('"mm_opt_ios_graphics"'), 4)
        path.write_text(
            source.replace('"mm_opt_ios_graphics"', '-- "mm_opt_ios_graphics"'),
            encoding="utf-8",
        )
        self.assert_fails(root, "graphics-profile options group is not covered by load, backup, save, and undo")

    def test_faction_pda_reversed_fallback_order_fails(self) -> None:
        root = self.make_fixture()
        path = root / "src/xrGame/ui/UIFactionWarWnd.cpp"
        authored_then_fallback = '''    m_background2 = UIHelper::CreateFrameLine(xml, "background", this, false);
    if (!m_background2)
        m_background = UIHelper::CreateFrameWindow(xml, "background", this, false);'''
        fallback_then_authored = '''    m_background = UIHelper::CreateFrameWindow(xml, "background", this, false);
    if (!m_background)
        m_background2 = UIHelper::CreateFrameLine(xml, "background", this, false);'''
        self.replace_once(path, authored_then_fallback, fallback_then_authored)
        self.assert_fails(root, "faction-war PDA must try three-slice/static assets before nine-slice fallbacks")

    def test_faction_pda_unconditional_fallback_fails(self) -> None:
        root = self.make_fixture()
        path = root / "src/xrGame/ui/UIFactionWarWnd.cpp"
        self.replace_once(path, '    if (!m_center_background2)\n',
                          '    // fallback made unconditional\n')
        self.assert_fails(root, "faction-war center fallback is not conditional")

    def test_focus_scroll_in_comment_fails(self) -> None:
        root = self.make_fixture()
        path = root / "src/xrGame/ui/UIDragDropListEx.cpp"
        self.replace_once(path, "                OnScrollV(nullptr, nullptr);",
                          "                // OnScrollV(nullptr, nullptr);")
        self.assert_fails(root, "iOS focused inventory cell does not retain the complete scroll contract")

    def test_focus_scroll_in_literal_inactive_branch_fails(self) -> None:
        root = self.make_fixture()
        path = root / "src/xrGame/ui/UIDragDropListEx.cpp"
        target = '''            const int target = xray::ui::ios_focus_geometry::RequestedVerticalScroll(
                m_vScrollBar->GetScrollPos(), policyViewport, policyItem);'''
        self.replace_once(path, target, "#if 0\n" + target + "\n#endif")
        self.assert_fails(root, "iOS focused inventory cell does not retain the complete scroll contract")

    def test_focus_scroll_warp_before_scroll_fails(self) -> None:
        root = self.make_fixture()
        path = root / "src/xrGame/ui/UIDragDropListEx.cpp"
        self.replace_once(
            path,
            '''                OnScrollV(nullptr, nullptr);
                UI().GetUICursor().WarpToWindow(focused);''',
            '''                UI().GetUICursor().WarpToWindow(focused);
                OnScrollV(nullptr, nullptr);''',
        )
        self.assert_fails(root, "iOS focus scroll must clamp, compare, scroll, then warp in that order")

    def test_focus_scroll_requested_position_is_not_a_clamp_check(self) -> None:
        root = self.make_fixture()
        path = root / "src/xrGame/ui/UIDragDropListEx.cpp"
        self.replace_once(path, "if (m_vScrollBar->GetScrollPos() != previous)",
                          "if (target != previous)")
        self.assert_fails(root, "iOS focused inventory cell does not retain the complete scroll contract")

    def test_focus_scroll_swapped_viewport_fields_fails(self) -> None:
        root = self.make_fixture()
        path = root / "src/xrGame/ui/UIDragDropListEx.cpp"
        self.replace_once(path, "viewport.x1, viewport.y1, viewport.x2, viewport.y2",
                          "viewport.x1, viewport.y1, viewport.y2, viewport.x2")
        self.assert_fails(root, "iOS focus scroll must map exact viewport/item fields into the requested target")

    def test_focus_scroll_detached_target_fails(self) -> None:
        root = self.make_fixture()
        path = root / "src/xrGame/ui/UIDragDropListEx.cpp"
        self.replace_once(path, "const int target = xray::ui::ios_focus_geometry::RequestedVerticalScroll(",
                          "const int ignoredTarget = xray::ui::ios_focus_geometry::RequestedVerticalScroll(")
        self.assert_fails(root, "iOS focus scroll must map exact viewport/item fields into the requested target")

    def test_focus_scroll_dead_runtime_branch_fails(self) -> None:
        root = self.make_fixture()
        path = root / "src/xrGame/ui/UIDragDropListEx.cpp"
        self.replace_once(path, "    if (CUIWindow* focused = UI().Focus().GetFocused())",
                          "    if (false)\n        return;\n\n    if (CUIWindow* focused = UI().Focus().GetFocused())")
        self.assert_fails(root, "iOS focus geometry contains a literal runtime if(false)/if(0) branch")

    def test_focus_scroll_dead_runtime_zero_branch_fails(self) -> None:
        root = self.make_fixture()
        path = root / "src/xrGame/ui/UIDragDropListEx.cpp"
        self.replace_once(path, "    if (CUIWindow* focused = UI().Focus().GetFocused())",
                          "    if (0)\n        return;\n\n    if (CUIWindow* focused = UI().Focus().GetFocused())")
        self.assert_fails(root, "iOS focus geometry contains a literal runtime if(false)/if(0) branch")

    def test_focus_clip_requires_every_parent_type(self) -> None:
        root = self.make_fixture()
        path = root / "src/xrGame/UIDialogHolder.cpp"
        self.replace_once(path, "smart_cast<CUIListWnd*>(parent)", "smart_cast<CUIWindow*>(parent)")
        self.assert_fails(root, "iOS focus frame does not retain every recognized parent clip and scissor")

    def test_focus_clip_drag_drop_uses_client_area(self) -> None:
        root = self.make_fixture()
        path = root / "src/xrGame/UIDialogHolder.cpp"
        self.replace_once(path, "inventory->GetClientArea(parent_clip);",
                          "inventory->GetAbsoluteRect(parent_clip);")
        self.assert_fails(root, "iOS focus frame does not retain every recognized parent clip and scissor")

    def test_focus_geometry_dead_runtime_branch_fails(self) -> None:
        root = self.make_fixture()
        path = root / "src/xrGame/UIDialogHolder.cpp"
        self.replace_once(path,
                          "clipState = xray::ui::ios_focus_geometry::AddClip(clipState, policyParentClip);",
                          "if (false) clipState = xray::ui::ios_focus_geometry::AddClip(clipState, policyParentClip);")
        self.assert_fails(root, "iOS focus geometry contains a literal runtime if(false)/if(0) branch")

    def test_focus_clip_detached_add_clip_result_fails(self) -> None:
        root = self.make_fixture()
        path = root / "src/xrGame/UIDialogHolder.cpp"
        self.replace_once(path,
                          "clipState = xray::ui::ios_focus_geometry::AddClip(clipState, policyParentClip);",
                          "const auto detachedClipState = xray::ui::ios_focus_geometry::AddClip(clipState, policyParentClip);")
        self.assert_fails(root, "iOS focus frame must assign the exact AddClip result back to clipState")

    def test_focus_clip_empty_visibility_branch_fails(self) -> None:
        root = self.make_fixture()
        path = root / "src/xrGame/UIDialogHolder.cpp"
        self.replace_once(path, "        if (!clipState.visible)\n            return;",
                          "        if (!clipState.visible)\n            ;")
        self.assert_fails(root, "iOS focus frame must return immediately when its clip is invisible")

    def test_focus_clip_swapped_policy_fields_fails(self) -> None:
        root = self.make_fixture()
        path = root / "src/xrGame/UIDialogHolder.cpp"
        self.replace_once(path, "parent_clip.x1, parent_clip.y1, parent_clip.x2, parent_clip.y2",
                          "parent_clip.x1, parent_clip.y1, parent_clip.y2, parent_clip.x2")
        self.assert_fails(root, "iOS focus frame must preserve exact policy clip-rect field mappings")

    def test_focus_clip_detached_scrollview_helper_fails(self) -> None:
        root = self.make_fixture()
        path = root / "src/xrGame/UIDialogHolder.cpp"
        self.replace_once(path, "parent_clip = scrollView->GetDrawClipRect();",
                          "scrollView->GetDrawClipRect();")
        self.assert_fails(root, "iOS focus frame must assign the shared ScrollView draw clip to its parent target")

    def test_focus_clip_push_scissor_cannot_be_removed(self) -> None:
        root = self.make_fixture()
        path = root / "src/xrGame/UIDialogHolder.cpp"
        self.replace_once(path, "        UI().PushScissor(clip);", "        // UI().PushScissor(clip);")
        self.assert_fails(root, "iOS focus frame does not retain every recognized parent clip and scissor")

    def test_focus_clip_pop_scissor_cannot_be_removed(self) -> None:
        root = self.make_fixture()
        path = root / "src/xrGame/UIDialogHolder.cpp"
        self.replace_once(path, "        UI().PopScissor();", "        // UI().PopScissor();")
        self.assert_fails(root, "iOS focus frame does not retain every recognized parent clip and scissor")

    def test_scrollview_draw_absolute_rect_regression_fails(self) -> None:
        root = self.make_fixture()
        path = root / "src/xrUICore/ScrollView/UIScrollView.cpp"
        self.replace_once(path, "const Frect visible_rect = GetDrawClipRect();",
                          "Frect visible_rect;\n    GetAbsoluteRect(visible_rect);")
        self.assert_fails(root, "CUIScrollView Draw must pass the shared helper result to scissor in draw order")

    def test_scrollview_draw_pushes_different_rect_fails(self) -> None:
        root = self.make_fixture()
        path = root / "src/xrUICore/ScrollView/UIScrollView.cpp"
        self.replace_once(path, "UI().PushScissor(visible_rect);", "UI().PushScissor(GetWndRect());")
        self.assert_fails(root, "CUIScrollView Draw must pass the shared helper result to scissor in draw order")

    def test_scrollview_dropped_indent_fails(self) -> None:
        root = self.make_fixture()
        path = root / "src/xrUICore/ScrollView/UIScrollView.cpp"
        self.replace_once(path, "    result.bottom -= m_downIndent;\n", "")
        self.assert_fails(root, "CUIScrollView draw clip helper must preserve both vertical indents")


if __name__ == "__main__":
    unittest.main()
