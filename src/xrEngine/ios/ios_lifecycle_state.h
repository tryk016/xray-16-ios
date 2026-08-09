#pragma once

#include <atomic>

namespace ios_lifecycle
{
inline constexpr int SkippedFrameBackoffMs = 10;

// iOS owns application activity through UIKit. A saved desktop
// rs_always_active value must never keep gameplay or timers running after the
// process has left the foreground.
constexpr bool EffectiveDeviceActive(const bool focused, const bool /*alwaysActive*/)
{
    return focused;
}

constexpr bool ShouldBypassPauseForAlwaysActive(const bool /*alwaysActive*/)
{
    return false;
}

// Report only a transition that the engine actually applied and retained.
// The durable foreground state may trigger a retry without a new activity
// notification, so this policy deliberately does not inspect ActivityChange.
constexpr bool ShouldReportProcessedTransition(const bool activityApplied,
    const bool focusBefore, const bool requestedForeground, const bool focusAfter)
{
    return activityApplied && focusBefore != focusAfter && focusAfter == requestedForeground;
}

template <typename DrainFn, typename BackoffFn>
void HandleSkippedFrame(DrainFn drain, BackoffFn backoff)
{
    drain();
    backoff(SkippedFrameBackoffMs);
}

// Activation callbacks may briefly unpause gameplay before the renderer has
// proved that its primary context survived restoration. Roll every callback
// back if the final validation fails so the next retry starts from the same
// suspended state as the original foreground transition.
template <typename ActivateFn, typename ValidateFn, typename RollbackFn>
bool CompleteActivation(ActivateFn activate, ValidateFn validate, RollbackFn rollback)
{
    activate();
    if (validate())
        return true;

    rollback();
    return false;
}

enum class ActivityChange : int
{
    None = -1,
    Inactive = 0,
    Active = 1,
};

struct PendingEvents
{
    ActivityChange activity{ ActivityChange::None };
    bool lowMemory{};
    bool persist{};
    bool foreground{ true };
};

// Persistence must run before deactivation: UIKit may suspend the process as
// soon as the background transition is acknowledged and the renderer/context
// shutdown is allowed to invoke code that depends on the current settings.
template <typename PersistFn, typename ActivityFn>
void ApplyActivityOrdered(const PendingEvents& events, const bool currentlyFocused,
    PersistFn persist, ActivityFn changeActivity)
{
    if (events.persist)
        persist();

    if (events.activity != ActivityChange::None || events.foreground != currentlyFocused)
        changeActivity(events.foreground);
}

// SDL event watches may run on a thread other than the engine thread. Keep the
// watch side lock-free and defer all engine, renderer and GL work to the main
// loop. Repeated notifications coalesce; the latest lifecycle state wins.
class EventInbox
{
public:
    void EnterBackground(const bool persist = false) { PublishActivity(false, persist); }
    void EnterForeground() { PublishActivity(true, false); }
    void LowMemory() { state.fetch_or(LowMemoryPending, std::memory_order_release); }

    PendingEvents Consume()
    {
        unsigned events = state.load(std::memory_order_acquire);
        for (;;)
        {
            const unsigned retained = events & (ForegroundState | FrameActive);
            if (state.compare_exchange_weak(events, retained,
                    std::memory_order_acq_rel, std::memory_order_acquire))
                break;
        }
        return {
            events & ActivityChanged
                ? (events & ForegroundState ? ActivityChange::Active : ActivityChange::Inactive)
                : ActivityChange::None,
            (events & LowMemoryPending) != 0,
            (events & PersistencePending) != 0,
            (events & ForegroundState) != 0,
        };
    }

    bool IsForeground() const { return (state.load(std::memory_order_acquire) & ForegroundState) != 0; }

    // Establish a total order between a lifecycle publication and the start of
    // a frame. If a pending transition wins, the frame is skipped and drained;
    // if this CAS wins, a later transition is ordered after the frame started.
    bool TryBeginFrame()
    {
        unsigned current = state.load(std::memory_order_acquire);
        for (;;)
        {
            if (!(current & ForegroundState) || (current & (PendingMask | FrameActive)))
                return false;
            const unsigned desired = current | FrameActive;
            if (state.compare_exchange_weak(current, desired,
                    std::memory_order_acq_rel, std::memory_order_acquire))
                return true;
        }
    }

    void EndFrame() { state.fetch_and(~FrameActive, std::memory_order_release); }

private:
    enum : unsigned
    {
        ActivityChanged = 1u << 0,
        LowMemoryPending = 1u << 1,
        PersistencePending = 1u << 2,
        ForegroundState = 1u << 3,
        FrameActive = 1u << 4,
        PendingMask = ActivityChanged | LowMemoryPending | PersistencePending,
    };

    void PublishActivity(const bool foreground, const bool persist)
    {
        unsigned current = state.load(std::memory_order_relaxed);
        for (;;)
        {
            unsigned desired = current | ActivityChanged;
            if (persist)
                desired |= PersistencePending;
            if (foreground)
                desired |= ForegroundState;
            else
                desired &= ~ForegroundState;
            if (state.compare_exchange_weak(current, desired,
                    std::memory_order_release, std::memory_order_relaxed))
                return;
        }
    }

    // Foreground state and its pending generation share one atomic word. A
    // consumer can therefore never observe the durable UIKit state without
    // also observing the transition that published it.
    std::atomic<unsigned> state{ ForegroundState };
};
} // namespace ios_lifecycle
