#include "src/xrEngine/ios/ios_lifecycle_state.h"
#include "src/xrEngine/ios/ios_input_lifecycle_policy.h"
#include "src/xrEngine/ios/ios_low_memory_policy.h"
#include "src/xrEngine/ios/ios_texture_eviction_policy.h"

#include <cstdlib>
#include <iostream>
#include <string>

namespace
{
void require(const bool condition, const char* message)
{
    if (!condition)
    {
        std::cerr << "FAIL: " << message << '\n';
        std::exit(1);
    }
}

bool ReportsProcessedTransitionFor(const ios_lifecycle::PendingEvents& events,
    const bool focusBefore, const bool focusAfterWhenApplied)
{
    bool activityApplied = false;
    bool focusAfter = focusBefore;
    ios_lifecycle::ApplyActivityOrdered(events, focusBefore, [] {},
        [&](const bool requestedForeground)
        {
            require(requestedForeground == events.foreground,
                "activity callback must receive the requested final state");
            focusAfter = focusAfterWhenApplied;
            activityApplied = true;
        });
    return ios_lifecycle::ShouldReportProcessedTransition(
        activityApplied, focusBefore, events.foreground, focusAfter);
}
} // namespace

int main()
{
    require(ios_lifecycle::EffectiveDeviceActive(true, true),
        "foreground iOS activity must remain active");
    require(!ios_lifecycle::EffectiveDeviceActive(false, true),
        "rsAlwaysActive must not keep an iOS app active in the background");
    require(!ios_lifecycle::ShouldBypassPauseForAlwaysActive(true),
        "rsAlwaysActive must not bypass the iOS gameplay pause");

    require(ReportsProcessedTransitionFor(
                { ios_lifecycle::ActivityChange::Inactive, false, false, false }, true, false),
        "successful deactivation must be reported");
    require(!ReportsProcessedTransitionFor(
                { ios_lifecycle::ActivityChange::Inactive, false, false, false }, false, false),
        "duplicate deactivation must not be reported");
    require(!ReportsProcessedTransitionFor(
                { ios_lifecycle::ActivityChange::Active, false, false, true }, false, false),
        "failed or rolled-back activation must not be reported");
    require(ReportsProcessedTransitionFor(
                { ios_lifecycle::ActivityChange::None, false, false, true }, false, true),
        "successful retry with activity=None must be reported from durable foreground state");
    require(!ReportsProcessedTransitionFor(
                { ios_lifecycle::ActivityChange::Active, false, false, true }, true, true),
        "duplicate activation must not be reported");
    require(!ReportsProcessedTransitionFor(
                { ios_lifecycle::ActivityChange::None, false, false, true }, true, false),
        "no callback must not report an unrelated focus value");

    ios_lifecycle::EventInbox coalescedInbox;
    coalescedInbox.EnterBackground();
    coalescedInbox.EnterForeground();
    const ios_lifecycle::PendingEvents coalescedEvents = coalescedInbox.Consume();
    require(coalescedEvents.activity == ios_lifecycle::ActivityChange::Active
            && coalescedEvents.foreground,
        "coalesced activity must retain its final foreground state");
    require(!ReportsProcessedTransitionFor(coalescedEvents, true, true),
        "coalesced notifications returning to the original focus must not be reported");

    require(ios_lifecycle::SkippedFrameBackoffMs > 0,
        "a lifecycle-skipped frame must retain a non-zero loop backoff");
    bool skippedFrameDrained = false;
    int skippedFrameBackoff = 0;
    ios_lifecycle::HandleSkippedFrame(
        [&skippedFrameDrained] { skippedFrameDrained = true; },
        [&skippedFrameDrained, &skippedFrameBackoff](const int milliseconds)
        {
            require(skippedFrameDrained, "skipped-frame lifecycle work must drain before sleeping");
            skippedFrameBackoff = milliseconds;
        });
    require(skippedFrameBackoff == ios_lifecycle::SkippedFrameBackoffMs,
        "skipped-frame handler must apply the configured backoff");

    bool activationPaused = true;
    const bool failedActivation = ios_lifecycle::CompleteActivation(
        [&activationPaused] { activationPaused = false; },
        [] { return false; },
        [&activationPaused] { activationPaused = true; });
    require(!failedActivation && activationPaused,
        "failed activation must roll gameplay and clocks back to suspended state");

    bool successfulActivationRolledBack = false;
    const bool successfulActivation = ios_lifecycle::CompleteActivation(
        [] {}, [] { return true; },
        [&successfulActivationRolledBack] { successfulActivationRolledBack = true; });
    require(successfulActivation && !successfulActivationRolledBack,
        "successful activation must not execute rollback callbacks");

    using ios_input_lifecycle::PlanDeactivation;
    require(!PlanDeactivation(false, false).releaseSyntheticTouchButton,
        "inactive touch must not synthesize a mouse release");
    require(PlanDeactivation(true, false).releaseSyntheticTouchButton,
        "touch-only deactivation must synthesize one mouse release");
    require(!PlanDeactivation(true, true).releaseSyntheticTouchButton,
        "touch plus SDL left button must share the receiver-wide release");

    std::string activityOrder;
    ios_lifecycle::ApplyActivityOrdered(
        { ios_lifecycle::ActivityChange::Inactive, false, true, false }, true,
        [&activityOrder] { activityOrder += "persist"; },
        [&activityOrder](const bool foreground)
        {
            require(!foreground, "background activity callback must receive foreground=false");
            activityOrder += ",deactivate";
        });
    require(activityOrder == "persist,deactivate",
        "persistence must complete before lifecycle deactivation");

    ios_lifecycle::EventInbox inbox;

    auto events = inbox.Consume();
    require(events.activity == ios_lifecycle::ActivityChange::None, "new inbox must have no activity change");
    require(!events.lowMemory, "new inbox must have no memory warning");
    require(!events.persist, "new inbox must have no persistence request");
    require(events.foreground, "new inbox snapshot must be foreground");
    require(inbox.IsForeground(), "new inbox must reflect foreground startup");
    require(inbox.TryBeginFrame(), "foreground inbox with no pending work must admit a frame");
    inbox.EndFrame();

    inbox.EnterBackground();
    inbox.EnterForeground();
    inbox.LowMemory();
    inbox.LowMemory();
    inbox.EnterBackground(true);
    inbox.EnterForeground();
    events = inbox.Consume();
    require(events.activity == ios_lifecycle::ActivityChange::Active, "latest lifecycle signal must win");
    require(events.lowMemory, "memory warnings must coalesce");
    require(events.persist, "persistence requests must coalesce");
    require(events.foreground, "latest foreground state must belong to the consumed snapshot");

    events = inbox.Consume();
    require(events.activity == ios_lifecycle::ActivityChange::None, "consume must clear activity change");
    require(!events.lowMemory, "consume must clear memory warning");
    require(!events.persist, "consume must clear persistence request");

    inbox.EnterForeground();
    inbox.EnterBackground();
    require(!inbox.TryBeginFrame(), "pending background transition must win the frame boundary");
    events = inbox.Consume();
    require(events.activity == ios_lifecycle::ActivityChange::Inactive, "background signal must override stale foreground");
    require(!inbox.IsForeground(), "persistent foreground state must ignore rsAlwaysActive-style policy");

    inbox.EnterForeground();
    events = inbox.Consume();
    require(events.foreground, "foreground recovery must be consumable");
    require(inbox.TryBeginFrame(), "consumed foreground transition must admit a frame");
    inbox.EnterBackground(true);
    inbox.EndFrame();
    events = inbox.Consume();
    require(events.activity == ios_lifecycle::ActivityChange::Inactive && events.persist,
        "background published after frame start must remain pending for the next boundary");

    ios_low_memory::Policy lowMemory;
    auto decision = lowMemory.Update(true, false, true, true, false);
    require(decision.trimCpu, "background warning must request a CPU-only trim");
    require(!decision.evictGpu, "background warning must not issue GL work");
    require(lowMemory.GpuEvictionPending(), "background warning must retain GPU eviction");

    decision = lowMemory.Update(false, true, true, false, false);
    require(!decision.evictGpu, "missing primary context must block GPU eviction");
    decision = lowMemory.Update(false, true, true, true, true);
    require(!decision.evictGpu, "active rendering must block GPU eviction");
    decision = lowMemory.Update(false, true, true, true, false);
    require(decision.evictGpu, "foreground primary-context safe point must evict exactly once");
    require(!lowMemory.GpuEvictionPending(), "successful GPU eviction must consume the request");
    require(!lowMemory.Update(false, true, true, true, false).evictGpu,
        "consumed warning must not evict a second batch");

    using namespace ios_texture_eviction;
    require(!IsCandidate(false, false, false, 1024, 1000, 0), "unloaded texture must not be evicted");
    require(!IsCandidate(true, true, false, 1024, 1000, 0), "$user$ texture must not be evicted");
    require(!IsCandidate(true, false, false, 0, 1000, 0), "zero-storage texture must not be evicted");
    require(!IsCandidate(true, false, true, 1024, 1000, 0), "video/sequence/user-backed texture must stay pinned");
    require(!IsCandidate(true, false, false, 1024, 1000, 800), "recent texture must not be evicted");
    require(IsCandidate(true, false, false, 1024, 1000, 600), "stale file-backed texture must be eligible");
    require(FitsBudget(BudgetBytes - 1024, 1024), "candidate exactly filling the budget must fit");
    require(!FitsBudget(BudgetBytes - 1024, 1025), "eviction must never exceed the per-warning budget");

    std::cout << "iOS lifecycle policy: PASS\n";
}
