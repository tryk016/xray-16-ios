#pragma once

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>

namespace ios_graphics
{
enum class Tier : unsigned
{
    Performance = 0,
    Balanced = 1,
    Quality = 2,
};

constexpr float NanosecondsToMilliseconds(const std::uint64_t nanoseconds)
{
    return static_cast<float>(nanoseconds) / 1000000.0f;
}

constexpr float NanosecondsToSeconds(const std::uint64_t nanoseconds)
{
    return static_cast<float>(nanoseconds) / 1000000000.0f;
}

class AdaptivePolicy
{
public:
    static constexpr float WindowSeconds = 2.0f;
    static constexpr float InitialWarmupSeconds = 5.0f;
    static constexpr float DowngradeCooldownSeconds = 8.0f;
    static constexpr float UpgradeCooldownSeconds = 20.0f;
    static constexpr float DowngradeP90Milliseconds = 31.0f;
    static constexpr float EmergencyP90Milliseconds = 40.0f;
    static constexpr float UpgradeP90Milliseconds = 24.0f;

    explicit AdaptivePolicy(const Tier initialTier = Tier::Balanced) { Reset(initialTier); }

    void Reset(const Tier tier, const float warmupSeconds = InitialWarmupSeconds)
    {
        currentTier = tier;
        warmupRemaining = warmupSeconds;
        cooldownRemaining = 0.0f;
        slowWindows = 0;
        fastWindows = 0;
        ClearWindow();
    }

    void Suspend()
    {
        warmupRemaining = InitialWarmupSeconds;
        slowWindows = 0;
        fastWindows = 0;
        ClearWindow();
    }

    Tier Observe(const float workMilliseconds, const float elapsedSeconds, const bool allowUpgrade)
    {
        if (elapsedSeconds <= 0.0f || workMilliseconds <= 0.0f)
            return currentTier;

        // Headroom gathered while a thermal or power constraint blocks an
        // upgrade must not become eligible after that constraint is lifted.
        // Keep slowWindows intact: constraints must never suppress a downgrade.
        if (!allowUpgrade)
            fastWindows = 0;

        if (elapsedSeconds > 0.25f)
        {
            Suspend();
            return currentTier;
        }

        if (warmupRemaining > 0.0f)
        {
            warmupRemaining = std::max(0.0f, warmupRemaining - elapsedSeconds);
            return currentTier;
        }

        if (cooldownRemaining > 0.0f)
        {
            cooldownRemaining = std::max(0.0f, cooldownRemaining - elapsedSeconds);
            return currentTier;
        }

        if (!allowUpgrade)
            windowAllowsUpgrade = false;
        if (sampleCount < samples.size())
            samples[sampleCount++] = workMilliseconds;
        windowElapsed += elapsedSeconds;

        if (windowElapsed < WindowSeconds || sampleCount == 0)
            return currentTier;

        const float p90 = Percentile90();
        const bool completedWindowAllowsUpgrade = windowAllowsUpgrade;
        ClearWindow();

        if (p90 > DowngradeP90Milliseconds)
        {
            ++slowWindows;
            fastWindows = 0;
        }
        else if (p90 < UpgradeP90Milliseconds)
        {
            if (completedWindowAllowsUpgrade)
                ++fastWindows;
            else
                fastWindows = 0;
            slowWindows = 0;
        }
        else
        {
            slowWindows = 0;
            fastWindows = 0;
        }

        if ((p90 > EmergencyP90Milliseconds || slowWindows >= 2) && currentTier != Tier::Performance)
        {
            currentTier = static_cast<Tier>(static_cast<unsigned>(currentTier) - 1);
            cooldownRemaining = DowngradeCooldownSeconds;
            slowWindows = 0;
            fastWindows = 0;
        }
        else if (completedWindowAllowsUpgrade && fastWindows >= 8 && currentTier != Tier::Quality)
        {
            currentTier = static_cast<Tier>(static_cast<unsigned>(currentTier) + 1);
            cooldownRemaining = UpgradeCooldownSeconds;
            slowWindows = 0;
            fastWindows = 0;
        }

        return currentTier;
    }

    Tier CurrentTier() const { return currentTier; }

private:
    void ClearWindow()
    {
        sampleCount = 0;
        windowElapsed = 0.0f;
        windowAllowsUpgrade = true;
    }

    float Percentile90() const
    {
        auto ordered = samples;
        std::sort(ordered.begin(), ordered.begin() + static_cast<std::ptrdiff_t>(sampleCount));
        const std::size_t index = (sampleCount * 9 - 1) / 10;
        return ordered[index];
    }

private:
    std::array<float, 512> samples{};
    std::size_t sampleCount{};
    float windowElapsed{};
    float warmupRemaining{};
    float cooldownRemaining{};
    unsigned slowWindows{};
    unsigned fastWindows{};
    bool windowAllowsUpgrade{ true };
    Tier currentTier{ Tier::Balanced };
};
} // namespace ios_graphics
