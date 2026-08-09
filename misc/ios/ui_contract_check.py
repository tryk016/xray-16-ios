#!/usr/bin/env python3
"""Static contracts for the iOS-only menu and Options surface."""

from pathlib import Path
import argparse
import re
import sys
import xml.etree.ElementTree as ET


DEFAULT_ROOT = Path(__file__).resolve().parents[2]
ROOT = DEFAULT_ROOT


def source_path(relative: str) -> Path:
    return ROOT / relative


OPTIONS_FILE = "res/gamedata/configs/ui/ui_mm_opt_ios_16.xml"
VIDEO_SCRIPT = "res/gamedata/scripts/ui_mm_opt_video.script"
VIDEO_ADV_SCRIPT = "res/gamedata/scripts/ui_mm_opt_video_adv.script"
OPTIONS_SCRIPT = "res/gamedata/scripts/ui_mm_opt_main.script"
MAIN_MENU_SOURCE = "src/xrGame/ui/UIMMShniaga.cpp"
PROFILE_SOURCE = "src/xrEngine/ios/ios_graphics_profile.cpp"
CONSOLE_COMMAND_SOURCE = "src/xrEngine/xr_ioc_cmd.cpp"
DEVICE_SOURCE = "src/xrEngine/device.cpp"
TASK_PDA_SOURCE = "src/xrGame/ui/UITaskWnd.cpp"
FACTION_PDA_SOURCE = "src/xrGame/ui/UIFactionWarWnd.cpp"
DRAG_DROP_SOURCE = "src/xrGame/ui/UIDragDropListEx.cpp"
DIALOG_HOLDER_SOURCE = "src/xrGame/UIDialogHolder.cpp"
SCROLL_VIEW_HEADER = "src/xrUICore/ScrollView/UIScrollView.h"
SCROLL_VIEW_SOURCE = "src/xrUICore/ScrollView/UIScrollView.cpp"


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def parse(path: Path) -> ET.Element:
    try:
        return ET.parse(path).getroot()
    except (OSError, ET.ParseError) as error:
        fail(f"{path.relative_to(ROOT)} is not valid XML: {error}")


def strip_lua_comments(source: str) -> str:
    source = re.sub(r"--\[(=*)\[.*?\]\1\]", "", source, flags=re.S)
    return re.sub(r"--[^\n]*", "", source)


def strip_cpp_comments(source: str) -> str:
    """Remove comments without treating comment markers inside literals as comments."""
    result: list[str] = []
    index = 0
    quote: str | None = None
    while index < len(source):
        character = source[index]
        if quote:
            result.append(character)
            if character == "\\" and index + 1 < len(source):
                result.append(source[index + 1])
                index += 2
                continue
            if character == quote:
                quote = None
            index += 1
            continue
        if character in ('"', "'"):
            quote = character
            result.append(character)
            index += 1
            continue
        if source.startswith("//", index):
            newline = source.find("\n", index)
            if newline < 0:
                break
            result.append("\n")
            index = newline + 1
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            if end < 0:
                result.extend("\n" for char in source[index:] if char == "\n")
                break
            result.extend("\n" for char in source[index:end + 2] if char == "\n")
            index = end + 2
            continue
        result.append(character)
        index += 1
    return "".join(result)


def extract_braced_block(source: str, start: int, description: str) -> str:
    opening = source.find("{", start)
    declaration_end = source.find(";", start, opening if opening >= 0 else len(source))
    if opening < 0 or declaration_end >= 0:
        fail(f"cannot locate body for {description}")
    depth = 0
    quote: str | None = None
    index = opening
    while index < len(source):
        character = source[index]
        if quote:
            if character == "\\" and index + 1 < len(source):
                # The escaped character cannot close the current literal.
                index += 2
                continue
            if character == quote:
                quote = None
            index += 1
            continue
        if character in ('"', "'"):
            quote = character
            index += 1
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return source[opening:index + 1]
        index += 1
    fail(f"cannot locate closing body for {description}")


def extract_function_body(source: str, signature: str, description: str) -> str:
    match = re.search(signature, source)
    if match is None:
        fail(f"cannot locate {description}")
    return extract_braced_block(source, match.end(), description)


def extract_preprocessor_block(source: str, start_pattern: str, description: str) -> str:
    directives = list(re.finditer(r"^[ \t]*#\s*(if|ifdef|ifndef|elif|else|endif)\b.*$", source, re.M))
    start_index = next(
        (index for index, directive in enumerate(directives)
         if re.fullmatch(start_pattern, directive.group(0).strip())),
        None,
    )
    if start_index is None:
        fail(f"cannot locate {description}")
    depth = 0
    for directive in directives[start_index:]:
        kind = directive.group(1)
        if kind in ("if", "ifdef", "ifndef"):
            depth += 1
        elif kind == "endif":
            depth -= 1
            if depth == 0:
                return source[directives[start_index].start():directive.end()]
    fail(f"cannot locate closing {description}")


def literal_preprocessor_condition(expression: str) -> bool | None:
    """Return a known #if condition, or None when it needs macro evaluation."""
    expression = expression.strip()
    if re.fullmatch(
        r"defined\s*(?:\(\s*XR_PLATFORM_APPLE_IOS\s*\)|XR_PLATFORM_APPLE_IOS)",
        expression,
    ):
        # This checker validates the iOS-only product contract, so its platform
        # branch is known active and the matching outer #else is unreachable.
        return True
    while expression.startswith("(") and expression.endswith(")"):
        expression = expression[1:-1].strip()
    if re.fullmatch(r"0[xX][0-9a-fA-F]+|[0-9]+", expression) is None:
        return None
    return int(expression, 0) != 0


def strip_cpp_literal_inactive_branches(source: str) -> str:
    """Mask code proven unreachable by literal #if 0 branches.

    This is deliberately not a full C preprocessor: macro-dependent conditions
    remain potentially active, except that XR_PLATFORM_APPLE_IOS is fixed true
    by the product contract. It prevents code in known-inactive outer and nested
    branches from satisfying a static contract.
    """
    output: list[str] = []
    frames: list[dict[str, bool]] = []
    active = True
    directive_pattern = re.compile(r"^[ \t]*#\s*(if|ifdef|ifndef|elif|else|endif)\b(.*)$")

    for line in source.splitlines(keepends=True):
        directive = directive_pattern.match(line)
        if directive is not None:
            kind, expression = directive.groups()
            if kind in ("if", "ifdef", "ifndef"):
                condition = literal_preprocessor_condition(expression) if kind == "if" else None
                branch_active = active and condition is not False
                frames.append({
                    "parent_active": active,
                    "branch_taken": condition is True,
                    "active": branch_active,
                })
                active = branch_active
            elif frames:
                frame = frames[-1]
                if kind == "elif":
                    condition = literal_preprocessor_condition(expression)
                    branch_active = (
                        frame["parent_active"]
                        and not frame["branch_taken"]
                        and condition is not False
                    )
                    if condition is True:
                        frame["branch_taken"] = True
                    frame["active"] = branch_active
                    active = branch_active
                elif kind == "else":
                    branch_active = frame["parent_active"] and not frame["branch_taken"]
                    frame["branch_taken"] = True
                    frame["active"] = branch_active
                    active = branch_active
                else:  # endif
                    frames.pop()
                    active = frames[-1]["active"] if frames else True
            output.append(line)
            continue
        output.append(line if active else re.sub(r"[^\n]", " ", line))
    return "".join(output)


def check_main_menu() -> None:
    source = strip_cpp_comments(source_path(MAIN_MENU_SOURCE).read_text(encoding="utf-8"))
    create_list = extract_function_body(
        source, r"void\s+CUIMMShniaga::CreateList\s*\(", "CUIMMShniaga::CreateList")
    ios_block = extract_preprocessor_block(
        create_list, r"#\s*if\s+defined\(XR_PLATFORM_APPLE_IOS\)",
        "iOS main-menu filter block")
    active_ios_block = strip_cpp_literal_inactive_branches(ios_block)
    filter_match = re.search(
        r"if\s*\(\s*xr_strcmp\s*\(\s*button_name\s*,\s*\"btn_net_game\"\s*\)\s*==\s*0\s*"
        r"\|\|\s*xr_strcmp\s*\(\s*button_name\s*,\s*\"btn_multiplayer\"\s*\)\s*==\s*0\s*\)\s*"
        r"continue\s*;",
        active_ios_block,
        re.S,
    )
    if filter_match is None:
        fail("iOS main-menu construction does not filter every multiplayer entry")

    allocation_position = create_list.find('xr_new<CUIStatic>("Button")')
    filter_position = create_list.find(ios_block) + filter_match.start()
    if allocation_position < 0 or allocation_position < filter_position:
        fail("multiplayer filtering must happen before the menu button is allocated")


def check_native_resolution() -> None:
    root = parse(source_path(OPTIONS_FILE))
    resolution = root.find("./tab_video/list_resolution")
    if resolution is None:
        fail("iOS Options is missing tab_video:list_resolution")
    if resolution.find("options_item") is not None:
        fail("iOS resolution must be read-only, not bound to vid_mode")
    if resolution.find("text") is None:
        fail("iOS resolution readout has no text style")

    script = strip_lua_comments(source_path(VIDEO_SCRIPT).read_text(encoding="utf-8"))
    required = (
        'xml:InitStatic("tab_video:list_resolution"',
        "device().width",
        "device().height",
    )
    missing = [fragment for fragment in required if fragment not in script]
    if missing:
        fail("resolution script does not use the native render dimensions")


def check_graphics_profiles() -> None:
    root = parse(source_path(OPTIONS_FILE))
    profile = root.find("./tab_video/list_ios_profile")
    if profile is None:
        fail("iOS Options is missing the graphics-profile selector")

    item = profile.find("options_item")
    expected = {
        "entry": "ios_graphics_profile",
        "group": "mm_opt_ios_graphics",
        "depend": "runtime",
    }
    if item is None or any(item.get(key) != value for key, value in expected.items()):
        fail("graphics-profile selector is not bound to the runtime iOS profile group")
    if profile.get("list_length") != "3":
        fail("graphics-profile selector must expose exactly three entries")

    options_xml = source_path(OPTIONS_FILE).read_text(encoding="utf-8")
    if root.find("./tab_video/list_presets") is not None or 'entry="_preset"' in options_xml:
        fail("legacy five-level _preset must not be exposed by iOS Options")
    if root.find("./tab_video/btn_advanced") is not None:
        fail("technical Advanced graphics settings must not be exposed on iOS")
    if 'entry="rs_always_active"' in options_xml:
        fail("iOS Options must not expose desktop always-active lifecycle policy")

    always_active_label = root.find("./video_adv/cap_always_active")
    always_active_control = root.find("./video_adv/check_always_active")
    if always_active_label is None or always_active_control is None:
        fail("iOS hidden Advanced pane must satisfy the shared Lua always-active node contract")
    if always_active_control.find("options_item") is not None:
        fail("iOS always-active compatibility control must remain unbound")

    video_adv_script = strip_lua_comments(source_path(VIDEO_ADV_SCRIPT).read_text(encoding="utf-8"))
    required_compatibility_calls = (
        r'^\s*xml:InitStatic\(\s*"video_adv:cap_always_active"\s*,',
        r'^\s*xml:InitCheck\(\s*"video_adv:check_always_active"\s*,',
    )
    if any(re.search(pattern, video_adv_script, re.M) is None
            for pattern in required_compatibility_calls):
        fail("shared Advanced Lua no longer constructs the hidden always-active compatibility nodes")

    video_script = strip_lua_comments(source_path(VIDEO_SCRIPT).read_text(encoding="utf-8"))
    required_video_fragments = (
        "tab_video:list_ios_profile",
        "cap_window_mode:Show",
        "window_mode:Show",
        "cap_renderer:Show",
        "handler.combo_renderer:Show",
    )
    if any(fragment not in video_script for fragment in required_video_fragments):
        fail("iOS Video script does not expose the profile and hide desktop-only controls")
    if "combo_preset" in video_script or "btn_advanced_graphic" in video_script:
        fail("iOS Video script still initializes a legacy preset or Advanced button")

    options_script = strip_lua_comments(source_path(OPTIONS_SCRIPT).read_text(encoding="utf-8"))
    if options_script.count('"mm_opt_ios_graphics"') != 4:
        fail("graphics-profile options group is not covered by load, backup, save, and undo")
    if "mm_opt_video_preset" in options_script or "OnPresetChanged" in options_script:
        fail("legacy preset lifecycle is still active in the iOS Options script")

    source = strip_cpp_comments(source_path(PROFILE_SOURCE).read_text(encoding="utf-8"))
    token_block = re.search(r"iosGraphicsProfileTokens\[\]\s*=\s*\{(.*?)\};", source, re.S)
    if token_block is None:
        fail("ios_graphics_profile has no token table")
    token_names = re.findall(r'\{\s*"([^"]+)"\s*,\s*[0-9]+\s*\}', token_block.group(1))
    if token_names != ["Performance", "Optimal", "Quality"]:
        fail(f"expected Performance/Optimal/Quality tokens, found {token_names}")

    required_runtime_knobs = (
        '"rs_vis_distance"',
        '"r__geometry_lod"',
        '"r2_ls_squality"',
        '"r2_slight_fade"',
    )
    apply_tier = extract_function_body(source, r"void\s+ApplyTier\s*\(", "ApplyTier")
    if any(knob not in apply_tier for knob in required_runtime_knobs):
        fail("profile controller is missing a runtime-safe quality control")
    forbidden_runtime_knobs = ('"texture_lod"', '"r3_msaa"', '"r2_ssao"', '"rs_v_sync"')
    if any(knob in apply_tier for knob in forbidden_runtime_knobs):
        fail("profile controller changes a restart-bound graphics setting at runtime")

    console_source = extract_function_body(
        strip_cpp_comments(source_path(CONSOLE_COMMAND_SOURCE).read_text(encoding="utf-8")),
        r"void\s+CCC_LoadCFG::Execute\s*\(", "CCC_LoadCFG::Execute")
    if "ios_graphics_profile_on_config_loaded();" not in console_source:
        fail("cfg_load can override iOS profile knobs without invalidating the selected profile")
    config_loaded = extract_function_body(
        source, r"void\s+ios_graphics_profile_on_config_loaded\s*\(",
        "ios_graphics_profile_on_config_loaded")
    if ("appliedProfile = u32(-1);" not in config_loaded
            or "ios_graphics_profile_before_frame();" not in config_loaded):
        fail("iOS profile invalidation does not force a full reapply after cfg_load")
    device_source = strip_cpp_comments(source_path(DEVICE_SOURCE).read_text(encoding="utf-8"))
    process_frame = extract_function_body(
        device_source, r"void\s+CRenderDevice::ProcessFrame\s*\(", "CRenderDevice::ProcessFrame")
    reassert_position = process_frame.find("ios_graphics_profile_before_frame();")
    frame_position = process_frame.find("if (!BeforeFrame())")
    if reassert_position < 0 or frame_position < 0 or reassert_position > frame_position:
        fail("iOS graphics profile must be reasserted before the next frame starts")

    render_end = extract_function_body(
        device_source, r"void\s+CRenderDevice::RenderEnd\s*\(\s*void\s*\)",
        "CRenderDevice::RenderEnd for the startup-pause contract")
    always_active_policy = re.compile(
        r"#if\s+defined\(XR_PLATFORM_APPLE_IOS\)\s*"
        r"const\s+bool\s+bypassStartupPause\s*=\s*"
        r"ios_lifecycle::ShouldBypassPauseForAlwaysActive\(\s*"
        r"psDeviceFlags\.test\(rsAlwaysActive\)\s*\)\s*;\s*"
        r"#else\s*const\s+bool\s+bypassStartupPause\s*=\s*"
        r"psDeviceFlags\.test\(rsAlwaysActive\)\s*;\s*#endif",
        re.S)
    if always_active_policy.search(render_end) is None:
        fail("iOS startup pause can bypass the lifecycle policy through rs_always_active")
    if "!bypassStartupPause" not in render_end or "!psDeviceFlags.test(rsAlwaysActive)" in render_end:
        fail("RenderEnd startup pause does not consume the platform-scoped lifecycle decision")


def check_pda_frame_fallbacks() -> None:
    task_source = extract_function_body(
        strip_cpp_comments(source_path(TASK_PDA_SOURCE).read_text(encoding="utf-8")),
        r"bool\s+CUITaskWnd::Init\s*\(", "CUITaskWnd::Init")
    frame_window = 'UIHelper::CreateFrameWindow(xml, "background", this, false)'
    frame_line = 'UIHelper::CreateFrameLine(xml, "background", this, false)'
    task_fallback = re.search(
        r"if\s*\(\s*!\s*" + re.escape(frame_window) + r"\s*\)\s*"
        r"std::ignore\s*=\s*" + re.escape(frame_line) + r"\s*;",
        task_source,
        re.S,
    )
    if task_fallback is None:
        fail("task PDA background is missing its frame fallback")

    faction_source = extract_function_body(
        strip_cpp_comments(source_path(FACTION_PDA_SOURCE).read_text(encoding="utf-8")),
        r"bool\s+CUIFactionWarWnd::Init\s*\(", "CUIFactionWarWnd::Init")
    authored_background = 'm_background2 = UIHelper::CreateFrameLine(xml, "background", this, false)'
    fallback_background = 'm_background = UIHelper::CreateFrameWindow(xml, "background", this, false)'
    authored_center = 'm_center_background2 = UIHelper::CreateStatic(xml, "center_background", this, false)'
    fallback_center = 'm_center_background = UIHelper::CreateFrameWindow(xml, "center_background", this, false)'
    positions = [faction_source.find(fragment) for fragment in (
        authored_background, fallback_background, authored_center, fallback_center
    )]
    if any(position < 0 for position in positions):
        fail("faction-war PDA is missing an authored topology or fallback")
    if not (positions[0] < positions[1] and positions[2] < positions[3]):
        fail("faction-war PDA must try three-slice/static assets before nine-slice fallbacks")
    background_fallback = re.search(
        re.escape(authored_background) + r"\s*;\s*if\s*\(\s*!m_background2\s*\)\s*"
        + re.escape(fallback_background) + r"\s*;",
        faction_source,
        re.S,
    )
    if background_fallback is None:
        fail("faction-war background fallback is not conditional")
    center_fallback = re.search(
        re.escape(authored_center) + r"\s*;\s*if\s*\(\s*!m_center_background2\s*\)\s*"
        + re.escape(fallback_center) + r"\s*;",
        faction_source,
        re.S,
    )
    if center_fallback is None:
        fail("faction-war center fallback is not conditional")


def check_ios_focus_geometry() -> None:
    drag_source = strip_cpp_comments(source_path(DRAG_DROP_SOURCE).read_text(encoding="utf-8"))
    drag_update = extract_function_body(
        drag_source, r"void\s+CUIDragDropListEx::Update\s*\(", "CUIDragDropListEx::Update")
    ios_focus_block = extract_preprocessor_block(
        drag_update, r"#\s*if\s+defined\(XR_PLATFORM_APPLE_IOS\)",
        "iOS drag-drop focus block")
    active_focus_block = strip_cpp_literal_inactive_branches(ios_focus_block)
    literal_dead_branch = re.compile(r"\bif\s*\(\s*(?:false|0)\s*\)")
    if literal_dead_branch.search(active_focus_block):
        fail("iOS focus geometry contains a literal runtime if(false)/if(0) branch")
    required_scroll_fragments = (
        "GetClientArea(viewport);",
        "item->GetAbsoluteRect(item_rect);",
        "ios_focus_geometry::RequestedVerticalScroll(",
        "m_vScrollBar->SetScrollPos(target);",
        "m_vScrollBar->GetScrollPos() != previous",
        "OnScrollV(nullptr, nullptr);",
        "UI().GetUICursor().WarpToWindow(focused);",
    )
    if any(fragment not in active_focus_block for fragment in required_scroll_fragments):
        fail("iOS focused inventory cell does not retain the complete scroll contract")

    viewport_item_mapping = re.compile(
        r"const\s+xray::ui::ios_focus_geometry::Rect\s+policyViewport\s*\{\s*"
        r"viewport\.x1\s*,\s*viewport\.y1\s*,\s*viewport\.x2\s*,\s*viewport\.y2\s*\}\s*;\s*"
        r"const\s+xray::ui::ios_focus_geometry::Rect\s+policyItem\s*\{\s*"
        r"item_rect\.x1\s*,\s*item_rect\.y1\s*,\s*item_rect\.x2\s*,\s*item_rect\.y2\s*\}\s*;\s*"
        r"const\s+int\s+target\s*=\s*xray::ui::ios_focus_geometry::RequestedVerticalScroll\s*\(\s*"
        r"m_vScrollBar->GetScrollPos\(\)\s*,\s*policyViewport\s*,\s*policyItem\s*\)\s*;",
        re.S,
    )
    if viewport_item_mapping.search(active_focus_block) is None:
        fail("iOS focus scroll must map exact viewport/item fields into the requested target")

    requested_position = active_focus_block.find("ios_focus_geometry::RequestedVerticalScroll(")
    set_position = active_focus_block.find("m_vScrollBar->SetScrollPos(target);")
    clamped_position = active_focus_block.find("m_vScrollBar->GetScrollPos() != previous")
    scroll_position = active_focus_block.find("OnScrollV(nullptr, nullptr);")
    warp_position = active_focus_block.find("UI().GetUICursor().WarpToWindow(focused);")
    update_position = drag_update.find("inherited::Update();")
    if not (0 <= requested_position < set_position < clamped_position < scroll_position < warp_position):
        fail("iOS focus scroll must clamp, compare, scroll, then warp in that order")
    if update_position < 0 or drag_update.find("OnScrollV(nullptr, nullptr);") > update_position:
        fail("iOS focus scroll must run before child Update")

    holder_source = strip_cpp_comments(source_path(DIALOG_HOLDER_SOURCE).read_text(encoding="utf-8"))
    focus_frame = strip_cpp_literal_inactive_branches(extract_function_body(
        holder_source, r"void\s+ios_draw_focus_frame\s*\(", "ios_draw_focus_frame"))
    if literal_dead_branch.search(focus_frame):
        fail("iOS focus geometry contains a literal runtime if(false)/if(0) branch")
    drag_parent = "smart_cast<CUIDragDropListEx*>(parent)"
    scroll_parent = "auto* scrollView = smart_cast<CUIScrollView*>(parent)"
    list_parent = "smart_cast<CUIListWnd*>(parent)"
    client_area = "inventory->GetClientArea(parent_clip);"
    scroll_draw_clip = "parent_clip = scrollView->GetDrawClipRect();"
    absolute_rect = "parent->GetAbsoluteRect(parent_clip);"
    required_clip_fragments = (
        drag_parent,
        scroll_parent,
        list_parent,
        client_area,
        absolute_rect,
        "if (!clipState.visible)",
        "UI().PushScissor(clip);",
        "UI().PopScissor();",
    )
    if any(fragment not in focus_frame for fragment in required_clip_fragments):
        fail("iOS focus frame does not retain every recognized parent clip and scissor")
    if scroll_draw_clip not in focus_frame:
        fail("iOS focus frame must assign the shared ScrollView draw clip to its parent target")

    add_clip_assignment = re.search(
        r"clipState\s*=\s*xray::ui::ios_focus_geometry::AddClip\s*\(\s*"
        r"clipState\s*,\s*policyParentClip\s*\)\s*;",
        focus_frame,
    )
    if add_clip_assignment is None:
        fail("iOS focus frame must assign the exact AddClip result back to clipState")

    drag_parent_position = focus_frame.find(drag_parent)
    client_area_position = focus_frame.find(client_area)
    scroll_position = focus_frame.find(scroll_parent)
    scroll_draw_clip_position = focus_frame.find(scroll_draw_clip)
    list_position = focus_frame.find(list_parent)
    absolute_rect_position = focus_frame.find(absolute_rect)
    clip_position = add_clip_assignment.start()
    invisible_position = focus_frame.find("if (!clipState.visible)")
    if not (drag_parent_position < client_area_position < scroll_position < scroll_draw_clip_position < list_position
            < absolute_rect_position
            < clip_position < invisible_position):
        fail("iOS focus frame maps drag-drop, scroll-view and list ancestors incorrectly")

    parent_clip_mapping = re.compile(
        r"const\s+xray::ui::ios_focus_geometry::Rect\s+policyParentClip\s*\{\s*"
        r"parent_clip\.x1\s*,\s*parent_clip\.y1\s*,\s*parent_clip\.x2\s*,\s*parent_clip\.y2\s*\}\s*;",
        re.S,
    )
    exact_clip_mapping = re.compile(
        r"clip\.set\(\s*clipState\.clip\.left\s*,\s*clipState\.clip\.top\s*,\s*"
        r"clipState\.clip\.right\s*,\s*clipState\.clip\.bottom\s*\)\s*;",
        re.S,
    )
    if parent_clip_mapping.search(focus_frame) is None or exact_clip_mapping.search(focus_frame) is None:
        fail("iOS focus frame must preserve exact policy clip-rect field mappings")
    immediate_invisible_return = re.compile(
        r"if\s*\(\s*!\s*clipState\.visible\s*\)\s*return\s*;",
        re.S,
    )
    if immediate_invisible_return.search(focus_frame) is None:
        fail("iOS focus frame must return immediately when its clip is invisible")

    push = re.search(
        r"if\s*\(\s*clipState\.hasClip\s*\)\s*UI\(\)\.PushScissor\(clip\)\s*;",
        focus_frame,
    )
    pop = re.search(
        r"if\s*\(\s*clipState\.hasClip\s*\)\s*UI\(\)\.PopScissor\(\)\s*;",
        focus_frame,
    )
    draw = focus_frame.find("GEnv.UIRender->StartPrimitive(")
    flush = focus_frame.find("GEnv.UIRender->FlushPrimitive();")
    if push is None or pop is None or not (push.start() < draw < flush < pop.start()):
        fail("iOS focus frame scissor must symmetrically bracket draw and flush")

    scroll_header = strip_cpp_comments(source_path(SCROLL_VIEW_HEADER).read_text(encoding="utf-8"))
    if re.search(r"\[\[nodiscard\]\]\s*Frect\s+GetDrawClipRect\s*\(\s*\)\s*const\s*;", scroll_header) is None:
        fail("CUIScrollView must expose a const exact draw-clip helper")

    scroll_source = strip_cpp_comments(source_path(SCROLL_VIEW_SOURCE).read_text(encoding="utf-8"))
    draw_clip = extract_function_body(
        scroll_source, r"Frect\s+CUIScrollView::GetDrawClipRect\s*\(\s*\)\s*const", "CUIScrollView::GetDrawClipRect")
    exact_draw_clip = re.compile(
        r"Frect\s+result\s*;\s*GetAbsoluteRect\(\s*result\s*\)\s*;\s*"
        r"result\.top\s*\+=\s*m_upIndent\s*;\s*result\.bottom\s*-=\s*m_downIndent\s*;\s*return\s+result\s*;",
        re.S,
    )
    if exact_draw_clip.search(draw_clip) is None:
        fail("CUIScrollView draw clip helper must preserve both vertical indents")
    scroll_draw = extract_function_body(
        scroll_source, r"void\s+CUIScrollView::Draw\s*\(", "CUIScrollView::Draw")
    draw_clip_consumption = re.search(
        r"const\s+Frect\s+visible_rect\s*=\s*GetDrawClipRect\(\s*\)\s*;\s*"
        r"UI\(\)\.PushScissor\(\s*visible_rect\s*\)\s*;",
        scroll_draw,
        re.S,
    )
    first_child_draw = scroll_draw.find("(*it)->Draw();")
    pop_scissor = scroll_draw.find("UI().PopScissor();")
    if (draw_clip_consumption is None or first_child_draw < 0 or pop_scissor < 0
            or not (draw_clip_consumption.end() <= first_child_draw < pop_scissor)):
        fail("CUIScrollView Draw must pass the shared helper result to scissor in draw order")


def main(argv: list[str] | None = None) -> None:
    global ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root", type=Path, default=DEFAULT_ROOT,
        help="repository root to inspect (default: this checker’s repository)")
    arguments = parser.parse_args(argv)
    ROOT = arguments.source_root.resolve()
    check_main_menu()
    check_native_resolution()
    check_graphics_profiles()
    check_pda_frame_fallbacks()
    check_ios_focus_geometry()
    print("iOS UI contract: PASS")


if __name__ == "__main__":
    main()
