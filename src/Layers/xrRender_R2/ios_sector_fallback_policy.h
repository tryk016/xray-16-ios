#pragma once

#include <cstdint>
#include <limits>

// This policy deliberately has no dependency on the renderer, CDB, Device or
// logging. Position must provide writable x/y/z members; the callback returns
// a sector identifier for the supplied position.
namespace xray::render::ios_sector_fallback
{
enum class StartupTrigger
{
    LevelLoad,
    QuickLoad,
};

enum class StartupPhase
{
    Inactive,
    Awaiting,
    UnresolvedReported,
    Resolved,
};

enum class StartupObservation
{
    Ignored,
    ReportUnresolved,
    ReportResolved,
};

struct PreparedTransition
{
    std::uint64_t epoch{};
    StartupTrigger trigger{ StartupTrigger::LevelLoad };
    StartupPhase expectedPhase{ StartupPhase::Inactive };
    StartupPhase nextPhase{ StartupPhase::Inactive };
    StartupObservation observation{ StartupObservation::Ignored };

    [[nodiscard]] constexpr bool valid() const
    {
        return observation != StartupObservation::Ignored;
    }
};

class CameraApplyBarrier
{
public:
    void Arm(const std::uint64_t generation)
    {
        baseline_ = generation;
        armed_ = generation < std::numeric_limits<std::uint64_t>::max();
    }

    void Disarm() { armed_ = false; }

    [[nodiscard]] bool Passed(const std::uint64_t generation) const
    {
        return armed_ && generation > baseline_;
    }

    [[nodiscard]] std::uint64_t baseline() const { return baseline_; }
    [[nodiscard]] bool armed() const { return armed_; }

private:
    std::uint64_t baseline_{};
    bool armed_{};
};

inline constexpr float ProbeRadii[] = { 0.5f, 1.f, 2.f, 4.f, 8.f, 16.f, 32.f };
inline constexpr float ProbeDirections[][2] =
{
    { 1.f, 0.f }, { -1.f, 0.f }, { 0.f, 1.f }, { 0.f, -1.f },
    { 0.70710678f, 0.70710678f }, { -0.70710678f, 0.70710678f },
    { 0.70710678f, -0.70710678f }, { -0.70710678f, -0.70710678f },
};

[[nodiscard]] constexpr const char* StartupTriggerName(const StartupTrigger trigger)
{
    return trigger == StartupTrigger::LevelLoad ? "level_load" : "quick_load";
}

class StartupEvidence
{
public:
    explicit constexpr StartupEvidence(const std::uint64_t initialEpoch = 0)
        : epoch_(initialEpoch)
    {
    }

    [[nodiscard]] bool BeginEpoch(const StartupTrigger trigger)
    {
        if (epoch_ == std::numeric_limits<std::uint64_t>::max())
        {
            Deactivate();
            return false;
        }

        ++epoch_;
        active_ = true;
        phase_ = StartupPhase::Awaiting;
        trigger_ = trigger;
        return true;
    }

    void Deactivate()
    {
        active_ = false;
        phase_ = StartupPhase::Inactive;
    }

    [[nodiscard]] PreparedTransition PrepareDetected(const bool resolved) const
    {
        if (!active_ || phase_ == StartupPhase::Resolved)
            return {};

        if (resolved)
            return Prepare(StartupPhase::Resolved, StartupObservation::ReportResolved);

        if (phase_ == StartupPhase::Awaiting)
            return Prepare(StartupPhase::UnresolvedReported, StartupObservation::ReportUnresolved);

        return {};
    }

    [[nodiscard]] PreparedTransition PrepareNoDetection(const bool lastSectorValid) const
    {
        if (!active_ || phase_ != StartupPhase::Awaiting)
            return {};

        if (lastSectorValid)
        {
            // Retaining an already valid sector is meaningful only for a
            // same-level quick load. A level load resets the sector contract.
            if (trigger_ != StartupTrigger::QuickLoad)
                return {};

            return Prepare(StartupPhase::Resolved, StartupObservation::ReportResolved);
        }

        return Prepare(StartupPhase::UnresolvedReported, StartupObservation::ReportUnresolved);
    }

    [[nodiscard]] bool CommitPrepared(const PreparedTransition& transition)
    {
        if (!active_ || !transition.valid() || transition.epoch != epoch_
            || transition.trigger != trigger_ || transition.expectedPhase != phase_)
            return false;

        const bool awaitingTransition = phase_ == StartupPhase::Awaiting
            && ((transition.observation == StartupObservation::ReportResolved
                    && transition.nextPhase == StartupPhase::Resolved)
                || (transition.observation == StartupObservation::ReportUnresolved
                    && transition.nextPhase == StartupPhase::UnresolvedReported));
        const bool recoveryTransition = phase_ == StartupPhase::UnresolvedReported
            && transition.observation == StartupObservation::ReportResolved
            && transition.nextPhase == StartupPhase::Resolved;
        if (!awaitingTransition && !recoveryTransition)
            return false;

        phase_ = transition.nextPhase;
        return true;
    }

    [[nodiscard]] bool active() const { return active_; }
    [[nodiscard]] std::uint64_t epoch() const { return epoch_; }
    [[nodiscard]] StartupPhase phase() const { return phase_; }
    [[nodiscard]] StartupTrigger trigger() const { return trigger_; }

private:
    [[nodiscard]] PreparedTransition Prepare(
        const StartupPhase nextPhase, const StartupObservation observation) const
    {
        return { epoch_, trigger_, phase_, nextPhase, observation };
    }

    std::uint64_t epoch_{ 0 };
    bool active_{ false };
    StartupPhase phase_{ StartupPhase::Inactive };
    StartupTrigger trigger_{ StartupTrigger::LevelLoad };
};

template <typename SectorId, typename Position>
struct Result
{
    SectorId sector;
    Position probePosition;
    float matchedRadius;
    bool fallbackAttempted;
};

template <typename SectorId, typename Position, typename DetectSector>
[[nodiscard]] Result<SectorId, Position> Resolve(const Position& cameraPosition,
    const SectorId exactSector, const SectorId invalidSector, DetectSector&& detectSector)
{
    Result<SectorId, Position> result{ exactSector, cameraPosition, 0.f, false };
    if (exactSector != invalidSector)
        return result;

    result.fallbackAttempted = true;

    for (const float radius : ProbeRadii)
    {
        for (const auto& direction : ProbeDirections)
        {
            Position probePosition = cameraPosition;
            probePosition.x += direction[0] * radius;
            probePosition.z += direction[1] * radius;

            const SectorId sector = detectSector(probePosition);
            if (sector != invalidSector)
                return { sector, probePosition, radius, true };
        }
    }

    return result;
}

template <typename SectorId, typename OnChanged>
[[nodiscard]] bool CommitValidSector(const SectorId sector, const SectorId invalidSector,
    SectorId& lastSector, OnChanged&& onChanged)
{
    if (sector == invalidSector)
        return false;

    if (sector != lastSector)
        onChanged(sector);

    lastSector = sector;
    return true;
}
} // namespace xray::render::ios_sector_fallback
