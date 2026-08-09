#pragma once

#include <cmath>
#include <limits>

namespace xray::ui::ios_focus_geometry
{
struct Rect
{
    float left;
    float top;
    float right;
    float bottom;
};

inline int FloorToIntSaturated(const double value)
{
    constexpr double minimum = static_cast<double>(std::numeric_limits<int>::min());
    constexpr double maximum = static_cast<double>(std::numeric_limits<int>::max());
    if (value <= minimum)
        return std::numeric_limits<int>::min();
    if (value >= maximum)
        return std::numeric_limits<int>::max();
    return static_cast<int>(std::floor(value));
}

inline int CeilToIntSaturated(const double value)
{
    constexpr double minimum = static_cast<double>(std::numeric_limits<int>::min());
    constexpr double maximum = static_cast<double>(std::numeric_limits<int>::max());
    if (value <= minimum)
        return std::numeric_limits<int>::min();
    if (value >= maximum)
        return std::numeric_limits<int>::max();
    return static_cast<int>(std::ceil(value));
}

inline bool IsFinite(const Rect& rect)
{
    return std::isfinite(rect.left) && std::isfinite(rect.top) && std::isfinite(rect.right)
        && std::isfinite(rect.bottom);
}

inline int RequestedVerticalScroll(const int currentScroll, const Rect& viewport, const Rect& item)
{
    if (!IsFinite(viewport) || !IsFinite(item))
        return currentScroll;

    // Item rectangles are in screen space. Recover their invariant content
    // edges from the current integer scroll so the next Update sees the same
    // feasible interval rather than chasing a fractional delta.
    const double contentTop = static_cast<double>(item.top) + currentScroll;
    const double contentBottom = static_cast<double>(item.bottom) + currentScroll;
    const double minimumScroll = contentBottom - viewport.bottom;
    const double maximumScroll = contentTop - viewport.top;
    if (!std::isfinite(minimumScroll) || !std::isfinite(maximumScroll))
        return currentScroll;

    // Fully exposing the item requires scroll >= ceil(bottom constraint) and
    // scroll <= floor(top constraint). Saturation makes out-of-range geometry
    // deterministic without a potentially undefined float-to-int conversion.
    const int minimum = CeilToIntSaturated(minimumScroll);
    const int maximum = FloorToIntSaturated(maximumScroll);
    if (minimum > maximum)
    {
        // A too-tall item, or fractional edges with no shared integer, cannot
        // be fully exposed. Keep its top visible; this is a fixed point.
        return maximum;
    }
    if (currentScroll < minimum)
        return minimum;
    if (currentScroll > maximum)
        return maximum;
    return currentScroll;
}

struct ClipState
{
    bool hasClip{false};
    bool visible{true};
    Rect clip{};
};

inline ClipState AddClip(const ClipState& state, const Rect& next)
{
    if (!state.hasClip)
        return {true, true, next};
    if (!state.visible)
        return state;

    const Rect intersection{
        std::fmax(state.clip.left, next.left),
        std::fmax(state.clip.top, next.top),
        std::fmin(state.clip.right, next.right),
        std::fmin(state.clip.bottom, next.bottom),
    };

    // Frect regards touching edges as intersecting. Keep that zero-extent
    // scissor rather than treating it as an early-returning gap.
    const bool visible = !(state.clip.left > next.right || state.clip.right < next.left
        || state.clip.top > next.bottom || state.clip.bottom < next.top);
    return {true, visible, intersection};
}
} // namespace xray::ui::ios_focus_geometry
