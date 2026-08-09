#include "src/Layers/xrRender_R2/ios_sector_fallback_policy.h"

#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <string>
#include <vector>

namespace
{
constexpr unsigned InvalidSector = 0xffffffffu;
constexpr float Diagonal = 0.70710678f;

struct Position
{
    float x;
    float y;
    float z;
};

struct ExpectedDirection
{
    float xDirection;
    float zDirection;
};

constexpr float ExpectedRadii[] = { 0.5f, 1.f, 2.f, 4.f, 8.f, 16.f, 32.f };
constexpr ExpectedDirection ExpectedDirections[] =
{
    { 1.f, 0.f }, { -1.f, 0.f }, { 0.f, 1.f }, { 0.f, -1.f },
    { Diagonal, Diagonal }, { -Diagonal, Diagonal },
    { Diagonal, -Diagonal }, { -Diagonal, -Diagonal },
};

void require(const bool condition, const char* const message)
{
    if (!condition)
    {
        std::cerr << "FAIL: " << message << '\n';
        std::exit(1);
    }
}

bool nearlyEqual(const float left, const float right)
{
    return std::fabs(left - right) < 0.00001f;
}

void requirePosition(const Position& actual, const Position& expected, const char* const message)
{
    require(nearlyEqual(actual.x, expected.x) && nearlyEqual(actual.y, expected.y)
            && nearlyEqual(actual.z, expected.z), message);
}

Position expectedProbe(const Position& camera, const unsigned radiusIndex, const unsigned directionIndex)
{
    const float radius = ExpectedRadii[radiusIndex];
    const ExpectedDirection direction = ExpectedDirections[directionIndex];
    return { camera.x + direction.xDirection * radius, camera.y,
        camera.z + direction.zDirection * radius };
}

void testExactSectorBypassesFallback()
{
    const Position camera{ 10.f, 20.f, -30.f };
    unsigned callbackCalls = 0;
    const auto result = xray::render::ios_sector_fallback::Resolve(camera, 42u, InvalidSector,
        [&callbackCalls](const Position&) {
            ++callbackCalls;
            return 77u;
        });

    require(callbackCalls == 0u, "valid exact sector must not probe the fallback");
    require(result.sector == 42u, "valid exact sector must be preserved");
    require(!result.fallbackAttempted, "valid exact sector must report no fallback");
    require(nearlyEqual(result.matchedRadius, 0.f), "exact result must not have a probe radius");
    requirePosition(result.probePosition, camera, "exact result must retain camera position metadata");
}

void testFullOrderAndNoHitMetadata()
{
    const Position camera{ 3.25f, 19.5f, -7.75f };
    std::vector<Position> observed;
    const auto result = xray::render::ios_sector_fallback::Resolve(camera, InvalidSector, InvalidSector,
        [&observed](const Position& probe) {
            observed.push_back(probe);
            return InvalidSector;
        });

    require(observed.size() == 56u, "fallback must issue seven radii times eight directions");
    for (unsigned radiusIndex = 0; radiusIndex < 7u; ++radiusIndex)
    {
        for (unsigned directionIndex = 0; directionIndex < 8u; ++directionIndex)
        {
            const unsigned index = radiusIndex * 8u + directionIndex;
            requirePosition(observed[index], expectedProbe(camera, radiusIndex, directionIndex),
                "fallback probe order or coordinates changed");
            require(nearlyEqual(observed[index].y, camera.y), "fallback must not change camera y");
        }
    }

    require(result.sector == InvalidSector, "no hit must retain the invalid sector");
    require(result.fallbackAttempted, "no hit must report that fallback ran");
    require(nearlyEqual(result.matchedRadius, 0.f), "no hit must not report a matched radius");
    requirePosition(result.probePosition, camera, "no hit must retain camera metadata");
}

void testFirstSmallestRadiusHitStopsImmediately()
{
    const Position camera{ 0.f, 8.f, 0.f };
    std::vector<Position> observed;
    const Position winningProbe = expectedProbe(camera, 0u, 1u);
    const auto result = xray::render::ios_sector_fallback::Resolve(camera, InvalidSector, InvalidSector,
        [&observed, &winningProbe](const Position& probe) {
            observed.push_back(probe);
            return nearlyEqual(probe.x, winningProbe.x) && nearlyEqual(probe.z, winningProbe.z) ? 17u : InvalidSector;
        });

    require(observed.size() == 2u, "first hit at the smallest radius must stop probing immediately");
    requirePosition(observed[0], expectedProbe(camera, 0u, 0u), "first fallback direction changed");
    requirePosition(observed[1], winningProbe, "second fallback direction changed");
    require(result.sector == 17u, "smallest-radius hit must be selected");
    require(result.fallbackAttempted, "fallback hit must report fallback metadata");
    require(nearlyEqual(result.matchedRadius, 0.5f), "smallest-radius hit must report its radius");
    requirePosition(result.probePosition, winningProbe, "fallback hit must retain winning probe metadata");
}

void testDirectionTieUsesDeclaredOrder()
{
    const Position camera{ 1.f, 2.f, 3.f };
    unsigned callbackCalls = 0;
    const auto result = xray::render::ios_sector_fallback::Resolve(camera, InvalidSector, InvalidSector,
        [&callbackCalls, &camera](const Position& probe) {
            ++callbackCalls;
            if (nearlyEqual(probe.x, camera.x + 0.5f) && nearlyEqual(probe.z, camera.z))
                return 101u;
            if (nearlyEqual(probe.x, camera.x - 0.5f) && nearlyEqual(probe.z, camera.z))
                return 102u;
            return InvalidSector;
        });

    require(callbackCalls == 1u, "direction tie must choose +X before -X");
    require(result.sector == 101u, "direction order must resolve same-radius hits deterministically");
    require(nearlyEqual(result.matchedRadius, 0.5f), "direction tie must preserve smallest radius");
    requirePosition(result.probePosition, expectedProbe(camera, 0u, 0u),
        "direction tie must retain the first direction probe");
}

void testCommitInvalidPreservesLastSector()
{
    unsigned lastSector = 17u;
    unsigned callbackCalls = 0u;
    const bool committed = xray::render::ios_sector_fallback::CommitValidSector(
        InvalidSector, InvalidSector, lastSector, [&callbackCalls](const unsigned) { ++callbackCalls; });

    require(!committed, "invalid sector must not commit");
    require(lastSector == 17u, "invalid sector must preserve the prior last sector");
    require(callbackCalls == 0u, "invalid sector must not notify");
}

void testCommitUnchangedValidSectorDoesNotNotify()
{
    unsigned lastSector = 23u;
    unsigned callbackCalls = 0u;
    const bool committed = xray::render::ios_sector_fallback::CommitValidSector(
        23u, InvalidSector, lastSector, [&callbackCalls](const unsigned) { ++callbackCalls; });

    require(committed, "unchanged valid sector must commit");
    require(lastSector == 23u, "unchanged valid sector must preserve its value");
    require(callbackCalls == 0u, "unchanged valid sector must not notify");
}

void testCommitChangedValidSectorNotifiesBeforeAssignment()
{
    unsigned lastSector = 31u;
    unsigned callbackCalls = 0u;
    unsigned callbackSector = InvalidSector;
    unsigned observedLastSector = InvalidSector;
    const bool committed = xray::render::ios_sector_fallback::CommitValidSector(
        47u, InvalidSector, lastSector, [&callbackCalls, &callbackSector, &observedLastSector, &lastSector](const unsigned sector) {
            ++callbackCalls;
            callbackSector = sector;
            observedLastSector = lastSector;
        });

    require(committed, "changed valid sector must commit");
    require(callbackCalls == 1u, "changed valid sector must notify exactly once");
    require(callbackSector == 47u, "changed valid sector must notify the new sector");
    require(observedLastSector == 31u, "callback must observe the old sector before assignment");
    require(lastSector == 47u, "changed valid sector must update the final last sector");
}

void testStartupInactiveAndDeactivatePreserveEpoch()
{
    using namespace xray::render::ios_sector_fallback;
    StartupEvidence evidence;

    require(!evidence.active(), "startup evidence must begin inactive");
    require(evidence.phase() == StartupPhase::Inactive, "inactive evidence must expose the inactive phase");
    require(evidence.epoch() == 0u, "startup evidence must begin at epoch zero");
    require(!evidence.PrepareDetected(true).valid(), "inactive detected result must be ignored");
    require(!evidence.PrepareNoDetection(false).valid(), "inactive no-detection result must be ignored");

    require(evidence.BeginEpoch(StartupTrigger::LevelLoad), "first level-load epoch must begin");
    require(evidence.epoch() == 1u, "first startup epoch must be one");
    evidence.Deactivate();
    require(!evidence.active(), "deactivate must make startup evidence inactive");
    require(evidence.phase() == StartupPhase::Inactive, "deactivate must restore inactive phase");
    require(evidence.epoch() == 1u, "deactivate must preserve the monotonic epoch");
    require(evidence.BeginEpoch(StartupTrigger::QuickLoad), "epoch after deactivate must begin");
    require(evidence.epoch() == 2u, "epoch after deactivate must continue monotonically");
}

void testStartupDetectedExactAndDuplicates()
{
    using namespace xray::render::ios_sector_fallback;
    StartupEvidence evidence;
    require(evidence.BeginEpoch(StartupTrigger::LevelLoad), "level-load epoch must begin");
    require(evidence.trigger() == StartupTrigger::LevelLoad, "level-load trigger must be retained");
    require(evidence.phase() == StartupPhase::Awaiting, "new epoch must await one outcome");
    const PreparedTransition prepared = evidence.PrepareDetected(true);
    require(prepared.valid(), "first detected valid sector must prepare a report");
    require(prepared.epoch == 1u, "prepared transition must capture its epoch");
    require(prepared.trigger == StartupTrigger::LevelLoad, "prepared transition must capture its trigger");
    require(prepared.expectedPhase == StartupPhase::Awaiting,
        "prepared transition must capture the expected phase");
    require(prepared.nextPhase == StartupPhase::Resolved,
        "valid detection must prepare the resolved phase");
    require(prepared.observation == StartupObservation::ReportResolved,
        "valid detection must prepare resolved evidence");
    require(evidence.phase() == StartupPhase::Awaiting,
        "preparing evidence must not mutate the policy state");
    require(evidence.CommitPrepared(prepared), "current prepared transition must commit");
    require(evidence.phase() == StartupPhase::Resolved, "valid detection must resolve the epoch");
    require(!evidence.CommitPrepared(prepared), "committed transition token must not be reusable");
    require(!evidence.PrepareDetected(true).valid(), "resolved epoch must ignore duplicate valid detection");
    require(!evidence.PrepareDetected(false).valid(), "resolved epoch must ignore later invalid detection");
    require(!evidence.PrepareNoDetection(false).valid(), "resolved epoch must ignore no-detection outcomes");
}

void testStartupDetectedUnresolvedThenRecovery()
{
    using namespace xray::render::ios_sector_fallback;
    StartupEvidence evidence;
    require(evidence.BeginEpoch(StartupTrigger::LevelLoad), "level-load recovery epoch must begin");
    const PreparedTransition unresolved = evidence.PrepareDetected(false);
    require(unresolved.observation == StartupObservation::ReportUnresolved,
        "first detected no-hit fallback must prepare unresolved");
    require(evidence.phase() == StartupPhase::Awaiting,
        "preparing unresolved evidence must not mutate the phase");
    require(evidence.CommitPrepared(unresolved), "unresolved transition must commit");
    require(evidence.phase() == StartupPhase::UnresolvedReported,
        "no-hit fallback must enter unresolved-reported phase");
    require(!evidence.PrepareDetected(false).valid(), "duplicate detected no-hit must be ignored");
    require(!evidence.PrepareNoDetection(true).valid(),
        "unresolved epoch must never recover through retained-sector evidence");
    const PreparedTransition recovery = evidence.PrepareDetected(true);
    require(recovery.observation == StartupObservation::ReportResolved,
        "later detected valid sector must prepare recovery");
    require(recovery.expectedPhase == StartupPhase::UnresolvedReported,
        "recovery must capture unresolved as its expected phase");
    require(evidence.CommitPrepared(recovery), "actual detected recovery must commit");
    require(evidence.phase() == StartupPhase::Resolved, "detected recovery must resolve the epoch");
}

void testStartupQuickLoadRetainedAndNone()
{
    using namespace xray::render::ios_sector_fallback;
    StartupEvidence evidence;
    require(evidence.BeginEpoch(StartupTrigger::QuickLoad), "quick-load retained epoch must begin");
    require(evidence.trigger() == StartupTrigger::QuickLoad, "quick-load trigger must be retained");
    const PreparedTransition retained = evidence.PrepareNoDetection(true);
    require(retained.observation == StartupObservation::ReportResolved,
        "quick load may prepare an already-valid retained sector");
    require(evidence.phase() == StartupPhase::Awaiting,
        "retained preparation must not consume the epoch");
    require(evidence.CommitPrepared(retained), "quick-load retained transition must commit");

    require(evidence.BeginEpoch(StartupTrigger::QuickLoad), "second quick-load epoch must begin");
    require(evidence.epoch() == 2u, "quick-load epochs must remain monotonic");
    const PreparedTransition none = evidence.PrepareNoDetection(false);
    require(none.observation == StartupObservation::ReportUnresolved,
        "quick load without detection or a retained sector must prepare none");
    require(evidence.CommitPrepared(none), "quick-load none transition must commit");
    require(!evidence.PrepareNoDetection(true).valid(),
        "unresolved quick load must not recover through retained-sector evidence");
    const PreparedTransition recovery = evidence.PrepareDetected(true);
    require(recovery.observation == StartupObservation::ReportResolved,
        "unresolved quick load may prepare recovery only through a later detected sector");
    require(evidence.CommitPrepared(recovery), "detected quick-load recovery must commit");
}

void testStartupLevelLoadRejectsRetained()
{
    using namespace xray::render::ios_sector_fallback;
    StartupEvidence evidence;
    require(evidence.BeginEpoch(StartupTrigger::LevelLoad), "level-load retained rejection epoch must begin");
    require(!evidence.PrepareNoDetection(true).valid(), "level load must reject retained-sector evidence");
    require(evidence.phase() == StartupPhase::Awaiting,
        "rejected level-load retained evidence must leave the epoch awaiting detection");
    const PreparedTransition detected = evidence.PrepareDetected(true);
    require(detected.valid(), "level load must remain able to prepare actual detection");
    require(evidence.CommitPrepared(detected), "level-load actual detection must commit");

    require(evidence.BeginEpoch(StartupTrigger::LevelLoad), "second level-load epoch must begin");
    const PreparedTransition none = evidence.PrepareNoDetection(false);
    require(none.observation == StartupObservation::ReportUnresolved,
        "level load without detection or a valid sector must prepare none");
    require(evidence.CommitPrepared(none), "level-load none transition must commit");
}

void testPreparedTransitionStalenessAndTamperingFailClosed()
{
    using namespace xray::render::ios_sector_fallback;
    StartupEvidence evidence;
    require(evidence.BeginEpoch(StartupTrigger::LevelLoad), "stale-token epoch must begin");
    const PreparedTransition stale = evidence.PrepareDetected(true);
    require(stale.valid(), "stale-token fixture must prepare");
    require(evidence.phase() == StartupPhase::Awaiting, "preparation must leave state unchanged");

    require(evidence.BeginEpoch(StartupTrigger::QuickLoad), "replacement epoch must begin");
    require(!evidence.CommitPrepared(stale), "transition from a prior epoch must be rejected");
    require(evidence.phase() == StartupPhase::Awaiting,
        "stale-token rejection must not mutate the replacement epoch");

    PreparedTransition wrongTrigger = evidence.PrepareDetected(true);
    wrongTrigger.trigger = StartupTrigger::LevelLoad;
    require(!evidence.CommitPrepared(wrongTrigger), "transition with a stale trigger must be rejected");
    require(evidence.phase() == StartupPhase::Awaiting,
        "trigger rejection must not mutate the current phase");

    PreparedTransition illegal = evidence.PrepareDetected(true);
    illegal.nextPhase = StartupPhase::UnresolvedReported;
    require(!evidence.CommitPrepared(illegal), "illegal prepared transition must be rejected");
    require(evidence.phase() == StartupPhase::Awaiting,
        "illegal transition rejection must not mutate the phase");

    const PreparedTransition deactivated = evidence.PrepareDetected(true);
    evidence.Deactivate();
    require(!evidence.CommitPrepared(deactivated), "deactivated evidence must reject a prepared token");
    require(evidence.phase() == StartupPhase::Inactive,
        "deactivated token rejection must preserve the inactive phase");
}

void testCameraApplyBarrierAndGenerationOverflow()
{
    using namespace xray::render::ios_sector_fallback;
    CameraApplyBarrier barrier;
    require(!barrier.armed(), "camera barrier must begin disarmed");
    require(!barrier.Passed(1u), "disarmed camera barrier must never pass");

    barrier.Arm(41u);
    require(barrier.armed(), "representable camera generation must arm the barrier");
    require(barrier.baseline() == 41u, "camera barrier must retain its baseline");
    require(!barrier.Passed(40u), "camera generation regression must not pass the barrier");
    require(!barrier.Passed(41u), "unchanged camera generation must not pass the barrier");
    require(barrier.Passed(42u), "next camera application must pass the barrier");
    barrier.Disarm();
    require(!barrier.Passed(42u), "disarmed camera barrier must reject later generations");

    barrier.Arm(std::numeric_limits<std::uint64_t>::max());
    require(!barrier.armed(), "saturated camera generation must fail closed");
    require(!barrier.Passed(std::numeric_limits<std::uint64_t>::max()),
        "saturated camera generation must never pass");
}

void testStartupEpochOverflowFailsClosed()
{
    using namespace xray::render::ios_sector_fallback;
    StartupEvidence evidence(std::numeric_limits<std::uint64_t>::max() - 1u);
    require(evidence.BeginEpoch(StartupTrigger::LevelLoad), "last representable epoch must begin");
    require(evidence.epoch() == std::numeric_limits<std::uint64_t>::max(),
        "last representable epoch must reach uint64 max without wrapping");
    require(!evidence.BeginEpoch(StartupTrigger::QuickLoad), "epoch overflow must fail closed");
    require(!evidence.active(), "overflowed evidence must be inactive");
    require(evidence.phase() == StartupPhase::Inactive, "overflowed evidence must expose inactive phase");
    require(evidence.epoch() == std::numeric_limits<std::uint64_t>::max(),
        "overflow must saturate the monotonic epoch");
    require(!evidence.PrepareDetected(true).valid(), "overflowed evidence must not prepare stale observations");
}

void testStartupTriggerNames()
{
    using namespace xray::render::ios_sector_fallback;
    require(std::string(StartupTriggerName(StartupTrigger::LevelLoad)) == "level_load",
        "level-load trigger spelling changed");
    require(std::string(StartupTriggerName(StartupTrigger::QuickLoad)) == "quick_load",
        "quick-load trigger spelling changed");
}
} // namespace

int main()
{
    testExactSectorBypassesFallback();
    testFullOrderAndNoHitMetadata();
    testFirstSmallestRadiusHitStopsImmediately();
    testDirectionTieUsesDeclaredOrder();
    testCommitInvalidPreservesLastSector();
    testCommitUnchangedValidSectorDoesNotNotify();
    testCommitChangedValidSectorNotifiesBeforeAssignment();
    testStartupInactiveAndDeactivatePreserveEpoch();
    testStartupDetectedExactAndDuplicates();
    testStartupDetectedUnresolvedThenRecovery();
    testStartupQuickLoadRetainedAndNone();
    testStartupLevelLoadRejectsRetained();
    testPreparedTransitionStalenessAndTamperingFailClosed();
    testCameraApplyBarrierAndGenerationOverflow();
    testStartupEpochOverflowFailsClosed();
    testStartupTriggerNames();
    std::cout << "iOS sector fallback policy: PASS\n";
    return 0;
}
