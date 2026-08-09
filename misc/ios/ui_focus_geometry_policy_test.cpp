#include "src/xrGame/ui/ios_ui_focus_geometry_policy.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <limits>
#include <iostream>

namespace
{
using xray::ui::ios_focus_geometry::AddClip;
using xray::ui::ios_focus_geometry::ClipState;
using xray::ui::ios_focus_geometry::Rect;
using xray::ui::ios_focus_geometry::RequestedVerticalScroll;

void require(const bool condition, const char* const message)
{
    if (!condition)
    {
        std::cerr << "FAIL: " << message << '\n';
        std::exit(1);
    }
}

void requireRect(const Rect& actual, const Rect& expected, const char* const message)
{
    require(actual.left == expected.left && actual.top == expected.top
            && actual.right == expected.right && actual.bottom == expected.bottom, message);
}

struct ScrollStep
{
    int scroll;
    Rect item;
};

int applyRequestedScroll(ScrollStep& state, const Rect& viewport, const int minimum, const int maximum)
{
    const int requested = RequestedVerticalScroll(state.scroll, viewport, state.item);
    const int applied = std::clamp(requested, minimum, maximum);
    const float displacement = static_cast<float>(applied - state.scroll);
    state.item.top -= displacement;
    state.item.bottom -= displacement;
    state.scroll = applied;
    return requested;
}

void requireFixedPoint(ScrollStep state, const Rect& viewport, const int minimum, const int maximum,
    const int expectedFirstScroll, const char* const message)
{
    applyRequestedScroll(state, viewport, minimum, maximum);
    require(state.scroll == expectedFirstScroll, message);
    const ScrollStep beforeSecondUpdate = state;
    applyRequestedScroll(state, viewport, minimum, maximum);
    require(state.scroll == expectedFirstScroll && state.item.top == beforeSecondUpdate.item.top
            && state.item.bottom == beforeSecondUpdate.item.bottom,
        "applying the request must reach an idempotent scroll fixed point after scrollbar clamping");
}

void testVisibleAndExactBoundariesRetainCurrentScroll()
{
    const Rect viewport{0.f, 10.f, 100.f, 30.f};
    require(RequestedVerticalScroll(12, viewport, {5.f, 15.f, 20.f, 25.f}) == 12,
        "already fully visible item must retain current scroll");
    require(RequestedVerticalScroll(12, viewport, {5.f, 10.f, 20.f, 20.f}) == 12,
        "item exactly on the top boundary must retain current scroll");
    require(RequestedVerticalScroll(12, viewport, {5.f, 20.f, 20.f, 30.f}) == 12,
        "item exactly on the bottom boundary must retain current scroll");
}

void testMultiStepScrollFixedPoints()
{
    const Rect viewport{0.f, 0.f, 100.f, 20.f};
    requireFixedPoint({10, {0.f, -3.f, 1.f, 2.f}}, viewport, -100, 100, 7,
        "top-clipped item must settle at its top bound");
    requireFixedPoint({10, {0.f, 18.f, 1.f, 24.f}}, viewport, -100, 100, 14,
        "bottom-clipped item must settle at its bottom bound");
    requireFixedPoint({10, {0.f, -2.2f, 1.f, 3.f}}, viewport, -100, 100, 7,
        "fractional item must settle at the floor top bound");
}

void testViewportHeightBoundaries()
{
    const Rect viewport{0.f, 0.f, 100.f, 20.f};
    requireFixedPoint({8, {0.f, 22.25f, 1.f, 42.f}}, viewport, -100, 100, 30,
        "item just below viewport height must fit at the shared integer bound");
    requireFixedPoint({8, {0.f, 22.f, 1.f, 42.f}}, viewport, -100, 100, 30,
        "item equal to viewport height must fit exactly");
    requireFixedPoint({8, {0.f, 22.25f, 1.f, 42.5f}}, viewport, -100, 100, 30,
        "item above viewport height must align its top deterministically");
    requireFixedPoint({8, {0.f, 22.1f, 1.f, 42.1f}}, viewport, -100, 100, 30,
        "fractional equal-height item without an integer fit must align its top");
}

void testExternalScrollBarClamping()
{
    const Rect viewport{0.f, 0.f, 100.f, 20.f};
    requireFixedPoint({10, {0.f, -15.f, 1.f, -10.f}}, viewport, 5, 50, 5,
        "minimum-clamped scrollbar must still become stable after geometry moves");
    requireFixedPoint({10, {0.f, 35.f, 1.f, 40.f}}, viewport, -50, 20, 20,
        "maximum-clamped scrollbar must still become stable after geometry moves");
}

void testNonFiniteAndOutOfRangeInputs()
{
    const Rect viewport{0.f, 0.f, 100.f, 20.f};
    require(RequestedVerticalScroll(7, viewport, {0.f, std::numeric_limits<float>::infinity(), 1.f, 2.f}) == 7,
        "non-finite item geometry must retain current scroll");
    require(RequestedVerticalScroll(7, {0.f, std::numeric_limits<float>::quiet_NaN(), 1.f, 2.f},
                {0.f, 1.f, 1.f, 2.f}) == 7,
        "non-finite viewport geometry must retain current scroll");
    const float largest = std::numeric_limits<float>::max();
    require(RequestedVerticalScroll(0, {0.f, -largest, 100.f, 0.f}, {0.f, largest, 1.f, largest})
            == std::numeric_limits<int>::max(),
        "positive out-of-range scroll must saturate");
    require(RequestedVerticalScroll(0, {0.f, 0.f, 100.f, largest}, {0.f, -largest, 1.f, -largest})
            == std::numeric_limits<int>::lowest(),
        "negative out-of-range scroll must saturate");
}

void testFirstClipIsUnchanged()
{
    const Rect first{1.f, 2.f, 3.f, 4.f};
    const ClipState state = AddClip({}, first);
    require(state.hasClip && state.visible, "first clip must start visible clipping");
    requireRect(state.clip, first, "first clip must be retained exactly");
}

void testNestedClipsIntersect()
{
    ClipState state = AddClip({}, {0.f, 0.f, 100.f, 100.f});
    state = AddClip(state, {10.f, 20.f, 80.f, 90.f});
    state = AddClip(state, {15.f, 25.f, 70.f, 85.f});
    require(state.hasClip && state.visible, "nested clips with overlap must stay visible");
    requireRect(state.clip, {15.f, 25.f, 70.f, 85.f}, "three nested clips must intersect in order");
}

void testFractionalClipIntersection()
{
    ClipState state = AddClip({}, {0.25f, 1.5f, 9.75f, 10.5f});
    state = AddClip(state, {1.125f, 0.5f, 8.875f, 8.25f});
    require(state.visible, "fractional overlapping clips must remain visible");
    requireRect(state.clip, {1.125f, 1.5f, 8.875f, 8.25f},
        "fractional clips must preserve exact intersection coordinates");
}

void testStrictGapIsInvisible()
{
    ClipState state = AddClip({}, {0.f, 0.f, 2.f, 2.f});
    state = AddClip(state, {2.1f, 0.f, 4.f, 2.f});
    require(state.hasClip && !state.visible, "strictly separated clips must be invisible");
}

void testTouchingEdgesRemainVisible()
{
    ClipState state = AddClip({}, {0.f, 0.f, 2.f, 2.f});
    state = AddClip(state, {2.f, 0.f, 4.f, 2.f});
    require(state.hasClip && state.visible, "touching edges must retain Frect visibility");
    requireRect(state.clip, {2.f, 0.f, 2.f, 2.f},
        "touching edges must retain a zero-width clip");
}
} // namespace

int main()
{
    testVisibleAndExactBoundariesRetainCurrentScroll();
    testMultiStepScrollFixedPoints();
    testViewportHeightBoundaries();
    testExternalScrollBarClamping();
    testNonFiniteAndOutOfRangeInputs();
    testFirstClipIsUnchanged();
    testNestedClipsIntersect();
    testFractionalClipIntersection();
    testStrictGapIsInvisible();
    testTouchingEdgesRemainVisible();
    std::cout << "iOS UI focus geometry policy: PASS\n";
    return 0;
}
