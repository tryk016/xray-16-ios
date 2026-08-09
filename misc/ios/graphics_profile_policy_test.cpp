#include "src/xrEngine/ios/ios_graphics_profile_policy.h"

#include <array>
#include <cstdlib>
#include <iostream>

using ios_graphics::AdaptivePolicy;
using ios_graphics::Tier;

namespace
{
constexpr float WindowStepSeconds = 0.25f;
constexpr unsigned WindowSteps = static_cast<unsigned>(AdaptivePolicy::WindowSeconds / WindowStepSeconds);

void require(const bool condition, const char* message)
{
    if (!condition)
    {
        std::cerr << "FAIL: " << message << '\n';
        std::exit(1);
    }
}

void observe(AdaptivePolicy& policy, const float workMilliseconds, const float elapsedSeconds,
    const bool allowUpgrade = true)
{
    policy.Observe(workMilliseconds, elapsedSeconds, allowUpgrade);
}

void feedSteps(AdaptivePolicy& policy, const float workMilliseconds, const unsigned count,
    const bool allowUpgrade = true)
{
    for (unsigned index = 0; index < count; ++index)
        observe(policy, workMilliseconds, WindowStepSeconds, allowUpgrade);
}

void feedWindow(AdaptivePolicy& policy, const float workMilliseconds, const bool allowUpgrade = true)
{
    feedSteps(policy, workMilliseconds, WindowSteps, allowUpgrade);
}

void feedHalfBlockedWindow(AdaptivePolicy& policy, const float workMilliseconds)
{
    static_assert(WindowSteps % 2 == 0, "test window must split evenly");
    feedSteps(policy, workMilliseconds, WindowSteps / 2, false);
    feedSteps(policy, workMilliseconds, WindowSteps / 2, true);
}

void expireWarmup(AdaptivePolicy& policy)
{
    feedSteps(policy, 25.0f,
        static_cast<unsigned>(AdaptivePolicy::InitialWarmupSeconds / WindowStepSeconds));
}

void expireCooldown(AdaptivePolicy& policy, const float seconds)
{
    feedSteps(policy, 25.0f, static_cast<unsigned>(seconds / WindowStepSeconds));
}

void requireTier(const AdaptivePolicy& policy, const Tier expected, const char* message)
{
    require(policy.CurrentTier() == expected, message);
}

template <std::size_t Count>
void feedMixedCadence(AdaptivePolicy& policy, const std::array<float, Count>& cadence,
    const float seconds, const bool allowUpgrade = true)
{
    constexpr float lowWorkMilliseconds = 20.0f;
    constexpr float highWorkMilliseconds = 35.0f;
    float elapsed = 0.0f;
    std::size_t sample = 0;

    while (elapsed + 0.0001f < seconds)
    {
        float delta = cadence[sample % cadence.size()];
        if (elapsed + delta > seconds)
            delta = seconds - elapsed;
        const float workMilliseconds = sample % 20 == 19 ? highWorkMilliseconds : lowWorkMilliseconds;
        observe(policy, workMilliseconds, delta, allowUpgrade);
        elapsed += delta;
        ++sample;
    }
}

template <std::size_t Count>
void requireCadenceFastDecision(const std::array<float, Count>& cadence, const char* message)
{
    AdaptivePolicy policy(Tier::Performance);
    policy.Reset(Tier::Performance, 0.0f);
    // Nineteen 20 ms samples followed by one 35 ms sample keep p90 below 24 ms.
    // This verifies percentile behavior rather than only a constant-work shortcut.
    feedMixedCadence(policy, cadence, AdaptivePolicy::WindowSeconds * 9.5f);
    requireTier(policy, Tier::Balanced, message);
}

template <std::size_t Count>
void requireCadenceSlowDecision(const std::array<float, Count>& cadence, const char* message)
{
    AdaptivePolicy policy(Tier::Quality);
    policy.Reset(Tier::Quality, 0.0f);
    float elapsed = 0.0f;
    std::size_t sample = 0;

    while (elapsed + 0.0001f < AdaptivePolicy::WindowSeconds * 2.5f)
    {
        float delta = cadence[sample % cadence.size()];
        if (elapsed + delta > AdaptivePolicy::WindowSeconds * 2.5f)
            delta = AdaptivePolicy::WindowSeconds * 2.5f - elapsed;
        // Nineteen 35 ms samples followed by one 20 ms sample keep p90 above 31 ms.
        observe(policy, sample % 20 == 19 ? 20.0f : 35.0f, delta);
        elapsed += delta;
        ++sample;
    }

    requireTier(policy, Tier::Balanced, message);
}

void testTimingConversion()
{
    require(ios_graphics::NanosecondsToMilliseconds(500000) == 0.5f,
        "nanosecond work timing must preserve sub-millisecond samples");
    const float oneFrameSeconds = ios_graphics::NanosecondsToSeconds(33333333);
    require(oneFrameSeconds > 0.033f && oneFrameSeconds < 0.034f,
        "nanosecond frame timing must retain fractional seconds");
}

void testStrictThresholds()
{
    // Contract: downgrade and emergency are strict greater-than comparisons.
    // A p90 exactly 31 or 40 ms is not an immediate tier change; only > 40 ms
    // is an emergency. Headroom is strict less-than: exactly 24 ms is neutral.
    AdaptivePolicy downgradeExact(Tier::Quality);
    downgradeExact.Reset(Tier::Quality, 0.0f);
    feedWindow(downgradeExact, AdaptivePolicy::DowngradeP90Milliseconds - 0.01f);
    feedWindow(downgradeExact, AdaptivePolicy::DowngradeP90Milliseconds);
    requireTier(downgradeExact, Tier::Quality, "p90 at or below 31 ms must not count as slow");

    AdaptivePolicy downgradeAbove(Tier::Quality);
    downgradeAbove.Reset(Tier::Quality, 0.0f);
    feedWindow(downgradeAbove, AdaptivePolicy::DowngradeP90Milliseconds + 0.01f);
    requireTier(downgradeAbove, Tier::Quality, "one non-emergency slow window must not skip a tier");
    feedWindow(downgradeAbove, AdaptivePolicy::DowngradeP90Milliseconds + 0.01f);
    requireTier(downgradeAbove, Tier::Balanced, "two p90 windows above 31 ms must lower one tier");

    AdaptivePolicy emergencyExact(Tier::Quality);
    emergencyExact.Reset(Tier::Quality, 0.0f);
    feedWindow(emergencyExact, AdaptivePolicy::EmergencyP90Milliseconds);
    requireTier(emergencyExact, Tier::Quality, "p90 exactly 40 ms must not use the emergency path");

    AdaptivePolicy emergencyBelow(Tier::Quality);
    emergencyBelow.Reset(Tier::Quality, 0.0f);
    feedWindow(emergencyBelow, AdaptivePolicy::EmergencyP90Milliseconds - 0.01f);
    requireTier(emergencyBelow, Tier::Quality, "p90 below 40 ms must not use the emergency path");
    feedWindow(emergencyBelow, AdaptivePolicy::EmergencyP90Milliseconds - 0.01f);
    requireTier(emergencyBelow, Tier::Balanced,
        "two non-emergency p90 windows below 40 ms but above 31 ms must still downgrade once");

    AdaptivePolicy emergencyAbove(Tier::Quality);
    emergencyAbove.Reset(Tier::Quality, 0.0f);
    feedWindow(emergencyAbove, AdaptivePolicy::EmergencyP90Milliseconds + 0.01f);
    requireTier(emergencyAbove, Tier::Balanced, "p90 above 40 ms must lower one tier immediately");

    AdaptivePolicy headroomExact(Tier::Performance);
    headroomExact.Reset(Tier::Performance, 0.0f);
    feedSteps(headroomExact, AdaptivePolicy::UpgradeP90Milliseconds, WindowSteps * 8);
    requireTier(headroomExact, Tier::Performance, "p90 exactly 24 ms must be neutral");

    AdaptivePolicy headroomAbove(Tier::Performance);
    headroomAbove.Reset(Tier::Performance, 0.0f);
    feedSteps(headroomAbove, AdaptivePolicy::UpgradeP90Milliseconds + 0.01f, WindowSteps * 8);
    requireTier(headroomAbove, Tier::Performance, "p90 above 24 ms must not count as fast");

    AdaptivePolicy headroomBelow(Tier::Performance);
    headroomBelow.Reset(Tier::Performance, 0.0f);
    feedSteps(headroomBelow, AdaptivePolicy::UpgradeP90Milliseconds - 0.01f, WindowSteps * 7);
    requireTier(headroomBelow, Tier::Performance, "seven fast windows must not upgrade");
    feedWindow(headroomBelow, AdaptivePolicy::UpgradeP90Milliseconds - 0.01f);
    requireTier(headroomBelow, Tier::Balanced, "eight p90 windows below 24 ms must raise one tier");
}

void testFullTierProgressionAndCooldowns()
{
    AdaptivePolicy policy(Tier::Quality);
    policy.Reset(Tier::Quality, 0.0f);

    feedWindow(policy, 32.0f);
    requireTier(policy, Tier::Quality, "first slow window must not lower Quality");
    feedWindow(policy, 32.0f);
    requireTier(policy, Tier::Balanced, "Quality must lower to Balanced after two slow windows");

    expireCooldown(policy, AdaptivePolicy::DowngradeCooldownSeconds);
    feedWindow(policy, 32.0f);
    requireTier(policy, Tier::Balanced, "first post-cooldown slow window must not skip Balanced");
    feedWindow(policy, 32.0f);
    requireTier(policy, Tier::Performance, "Balanced must lower to Performance after two fresh slow windows");

    expireCooldown(policy, AdaptivePolicy::DowngradeCooldownSeconds);
    feedSteps(policy, 20.0f, WindowSteps * 7);
    requireTier(policy, Tier::Performance, "seven fast windows must not raise Performance");
    feedWindow(policy, 20.0f);
    requireTier(policy, Tier::Balanced, "Performance must raise to Balanced after eight fast windows");

    expireCooldown(policy, AdaptivePolicy::UpgradeCooldownSeconds);
    feedSteps(policy, 20.0f, WindowSteps * 7);
    requireTier(policy, Tier::Balanced, "upgrade cooldown must require eight new fast windows");
    feedWindow(policy, 20.0f);
    requireTier(policy, Tier::Quality, "Balanced must raise to Quality one tier at a time");
}

void testNeutralWindowsResetHysteresis()
{
    AdaptivePolicy slow(Tier::Quality);
    slow.Reset(Tier::Quality, 0.0f);
    feedWindow(slow, 32.0f);
    feedWindow(slow, AdaptivePolicy::DowngradeP90Milliseconds);
    feedWindow(slow, 32.0f);
    requireTier(slow, Tier::Quality, "neutral p90 window must reset slow hysteresis");
    feedWindow(slow, 32.0f);
    requireTier(slow, Tier::Balanced, "slow hysteresis must restart after neutral p90 window");

    AdaptivePolicy fast(Tier::Performance);
    fast.Reset(Tier::Performance, 0.0f);
    feedSteps(fast, 20.0f, WindowSteps * 7);
    feedWindow(fast, AdaptivePolicy::UpgradeP90Milliseconds);
    feedWindow(fast, 20.0f);
    requireTier(fast, Tier::Performance, "neutral p90 window must reset fast hysteresis");
    feedSteps(fast, 20.0f, WindowSteps * 7);
    requireTier(fast, Tier::Balanced, "fast hysteresis must restart after neutral p90 window");
}

void testUpgradeConstraintRequiresFreshEvidence()
{
    AdaptivePolicy blockedUpgrade(Tier::Performance);
    blockedUpgrade.Reset(Tier::Performance, 0.0f);
    feedSteps(blockedUpgrade, 20.0f, WindowSteps * 8, false);
    requireTier(blockedUpgrade, Tier::Performance, "allowUpgrade=false must block headroom upgrades");
    feedWindow(blockedUpgrade, 20.0f, true);
    requireTier(blockedUpgrade, Tier::Performance,
        "re-enabling upgrades must discard headroom observed while upgrades were blocked");
    feedSteps(blockedUpgrade, 20.0f, WindowSteps * 7, true);
    requireTier(blockedUpgrade, Tier::Balanced, "re-enabled upgrades need eight fresh fast windows");

    AdaptivePolicy mixedFastWindow(Tier::Performance);
    mixedFastWindow.Reset(Tier::Performance, 0.0f);
    feedHalfBlockedWindow(mixedFastWindow, 20.0f);
    requireTier(mixedFastWindow, Tier::Performance,
        "a false-to-true fast window must remain ineligible for an upgrade");
    feedSteps(mixedFastWindow, 20.0f, WindowSteps * 7, true);
    requireTier(mixedFastWindow, Tier::Performance,
        "a mixed-eligibility fast window must not count toward eight fresh windows");
    feedWindow(mixedFastWindow, 20.0f, true);
    requireTier(mixedFastWindow, Tier::Balanced,
        "a cleared window must restore eligibility for eight complete fresh fast windows");

    AdaptivePolicy mixedSlowWindow(Tier::Quality);
    mixedSlowWindow.Reset(Tier::Quality, 0.0f);
    feedHalfBlockedWindow(mixedSlowWindow, 32.0f);
    requireTier(mixedSlowWindow, Tier::Quality,
        "one mixed-eligibility slow window must preserve its p90 without skipping a tier");
    feedHalfBlockedWindow(mixedSlowWindow, 32.0f);
    requireTier(mixedSlowWindow, Tier::Balanced,
        "mixed blocked slow windows must still increment slow hysteresis and downgrade");

    AdaptivePolicy resetEligibility(Tier::Performance);
    resetEligibility.Reset(Tier::Performance, 0.0f);
    feedSteps(resetEligibility, 20.0f, WindowSteps / 2, false);
    resetEligibility.Reset(Tier::Performance, 0.0f);
    feedSteps(resetEligibility, 20.0f, WindowSteps * 8, true);
    requireTier(resetEligibility, Tier::Balanced, "Reset must restore window upgrade eligibility");

    AdaptivePolicy suspendEligibility(Tier::Performance);
    suspendEligibility.Reset(Tier::Performance, 0.0f);
    feedSteps(suspendEligibility, 20.0f, WindowSteps / 2, false);
    suspendEligibility.Suspend();
    expireWarmup(suspendEligibility);
    feedSteps(suspendEligibility, 20.0f, WindowSteps * 8, true);
    requireTier(suspendEligibility, Tier::Balanced, "Suspend must restore window upgrade eligibility");
}

void testGapsInvalidTimingAndSuspend()
{
    AdaptivePolicy exactGap(Tier::Quality);
    exactGap.Reset(Tier::Quality, 0.0f);
    feedWindow(exactGap, 45.0f);
    requireTier(exactGap, Tier::Balanced, "an elapsed gap exactly 0.25 s must remain a valid sample");

    AdaptivePolicy longGap(Tier::Quality);
    longGap.Reset(Tier::Quality, 0.0f);
    observe(longGap, 45.0f, 0.2501f);
    feedSteps(longGap, 45.0f,
        static_cast<unsigned>(AdaptivePolicy::InitialWarmupSeconds / WindowStepSeconds) - 1);
    requireTier(longGap, Tier::Quality, "a gap above 0.25 s must restart warmup");
    observe(longGap, 45.0f, WindowStepSeconds);
    feedWindow(longGap, 45.0f);
    requireTier(longGap, Tier::Balanced, "the first post-warmup emergency window must be observed normally");

    AdaptivePolicy ignored(Tier::Quality);
    ignored.Reset(Tier::Quality, 0.0f);
    feedWindow(ignored, 32.0f);
    observe(ignored, 0.0f, WindowStepSeconds);
    observe(ignored, -1.0f, WindowStepSeconds);
    observe(ignored, 32.0f, 0.0f);
    observe(ignored, 32.0f, -WindowStepSeconds);
    feedWindow(ignored, 32.0f);
    requireTier(ignored, Tier::Balanced, "zero or negative timing/work must not reset valid hysteresis");

    AdaptivePolicy suspended(Tier::Quality);
    suspended.Reset(Tier::Quality, 0.0f);
    feedWindow(suspended, 32.0f);
    suspended.Suspend();
    expireWarmup(suspended);
    feedWindow(suspended, 32.0f);
    requireTier(suspended, Tier::Quality, "Suspend must clear partial slow hysteresis");
    feedWindow(suspended, 32.0f);
    requireTier(suspended, Tier::Balanced, "Suspend recovery must require fresh slow windows");
}

void testCooldownExpiryBoundaries()
{
    AdaptivePolicy downgrade(Tier::Quality);
    downgrade.Reset(Tier::Quality, 0.0f);
    feedWindow(downgrade, 32.0f);
    feedWindow(downgrade, 32.0f);
    requireTier(downgrade, Tier::Balanced, "precondition: downgrade cooldown must start");

    feedSteps(downgrade, 45.0f,
        static_cast<unsigned>(AdaptivePolicy::DowngradeCooldownSeconds / WindowStepSeconds) - 1);
    feedSteps(downgrade, 45.0f, WindowSteps);
    requireTier(downgrade, Tier::Balanced,
        "cooldown-expiry sample must not also complete an emergency p90 window");
    observe(downgrade, 45.0f, WindowStepSeconds);
    requireTier(downgrade, Tier::Performance,
        "first complete emergency window after exact cooldown expiry must lower one tier");

    AdaptivePolicy upgrade(Tier::Performance);
    upgrade.Reset(Tier::Performance, 0.0f);
    feedSteps(upgrade, 20.0f, WindowSteps * 8);
    requireTier(upgrade, Tier::Balanced, "precondition: upgrade cooldown must start");
    feedSteps(upgrade, 20.0f,
        static_cast<unsigned>(AdaptivePolicy::UpgradeCooldownSeconds / WindowStepSeconds) - 1);
    feedSteps(upgrade, 20.0f, WindowSteps);
    requireTier(upgrade, Tier::Balanced,
        "upgrade cooldown-expiry sample must not count toward an old headroom window");
    feedSteps(upgrade, 20.0f, WindowSteps * 7 + 1);
    requireTier(upgrade, Tier::Quality,
        "upgrade must require eight complete windows after exact cooldown expiry");
}

void testCadenceAndBoundedSampleBehavior()
{
    constexpr std::array<float, 1> cadence30Hz{ 1.0f / 30.0f };
    constexpr std::array<float, 1> cadence60Hz{ 1.0f / 60.0f };
    constexpr std::array<float, 1> cadence120Hz{ 1.0f / 120.0f };
    constexpr std::array<float, 5> unevenCadence{ 0.007f, 0.011f, 0.014f, 0.019f, 0.023f };
    constexpr std::array<float, 1> highCadence{ 1.0f / 512.0f };

    requireCadenceFastDecision(cadence30Hz, "30 Hz cadence must produce the fast p90 decision");
    requireCadenceFastDecision(cadence60Hz, "60 Hz cadence must produce the fast p90 decision");
    requireCadenceFastDecision(cadence120Hz, "120 Hz cadence must produce the fast p90 decision");
    requireCadenceFastDecision(unevenCadence, "uneven valid cadence must produce the fast p90 decision");
    requireCadenceSlowDecision(cadence30Hz, "30 Hz cadence must produce the slow p90 decision");
    requireCadenceSlowDecision(cadence60Hz, "60 Hz cadence must produce the slow p90 decision");
    requireCadenceSlowDecision(cadence120Hz, "120 Hz cadence must produce the slow p90 decision");
    requireCadenceSlowDecision(unevenCadence, "uneven valid cadence must produce the slow p90 decision");

    // A two-second window at 512 Hz has 1,024 observations, intentionally more
    // than the policy's bounded sample store. The public outcome must still be
    // a single, frequency-independent tier decision without relying on private
    // capacity fields.
    requireCadenceFastDecision(highCadence,
        "bounded high-cadence sampling must retain the same p90 tier decision");
}
} // namespace

int main()
{
    testTimingConversion();
    testStrictThresholds();
    testFullTierProgressionAndCooldowns();
    testNeutralWindowsResetHysteresis();
    testUpgradeConstraintRequiresFreshEvidence();
    testGapsInvalidTimingAndSuspend();
    testCooldownExpiryBoundaries();
    testCadenceAndBoundedSampleBehavior();

    std::cout << "iOS graphics profile policy: PASS\n";
}
