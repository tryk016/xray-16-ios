#pragma once

namespace ios_input_lifecycle
{
struct DeactivationPlan
{
    bool releaseSyntheticTouchButton{};
};

constexpr DeactivationPlan PlanDeactivation(const bool fingerActive, const bool mouseLeftActive)
{
    // Touch is translated directly to the engine receiver and is therefore not
    // represented in SDL's mouse bitset. If a real/synthetic SDL left button is
    // already down, the receiver-wide release pass will release the shared
    // logical MOUSE_1 state exactly once.
    return { fingerActive && !mouseLeftActive };
}
} // namespace ios_input_lifecycle
