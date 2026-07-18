// iOS AVAudioSession lifecycle shim (audit P1).
//
// Problem: an audio-session interruption (Siri, phone call, timer/alarm, another
// non-mixable app) deactivates our session and stops the CoreAudio IO unit that
// OpenAL Soft renders into. Neither SDL (audio subsystem unused) nor OpenAL Soft
// reactivates the session or restarts the unit, so the game stays permanently
// silent once the interruption ends. This shim owns the AVAudioSession: category
// and activation at startup, then interruption/route-change/foreground handling
// for the life of the process.
//
// Engine-header-free on purpose: Common/PlatformApple.inl typedefs BOOL/WORD/etc.
// (BOOL as int32_t) which collide with the Objective-C runtime's BOOL, so xrCore
// headers must not enter an Objective-C++ TU. The two xrCore log entry points are
// declared by hand below instead.
//
// ARC: cmake/toolchains/ios.toolchain.cmake defaults ENABLE_ARC=ON, so this file
// is compiled with -fobjc-arc. It is written to be correct under MRC as well: no
// retain/release/autorelease, no __bridge casts, no ObjC statics; block-based
// observers are strongly held by the notification center itself (documented
// behavior), so the returned tokens are deliberately not stored.

#include "ios_audio_session.h"

#if defined(IOS_AUDIO_SESSION_SUPPORTED)

#import <AVFoundation/AVFoundation.h>
#import <UIKit/UIKit.h>

// OpenAL Soft is linked statically on iOS (cmake/ios/deps builds openal-soft
// 1.25.2), so the ALC_SOFT_pause_device entry points always exist at link time.
#define AL_ALEXT_PROTOTYPES
#include <alc.h>
#include <alext.h>

// xrCore log (see file comment for why these are hand-declared instead of
// including xrCore/log.h): iOS is always a static build (XRAY_STATIC_BUILD ->
// XRCORE_API expands to nothing), pcstr is const char*, and __cdecl is a no-op
// on arm64 — these declarations mangle identically to the definitions.
void Msg(const char* format, ...);
void Log(const char* msg);

namespace
{
ios_audio::callback g_on_suspend = nullptr;
ios_audio::callback g_on_resume = nullptr;

// Only touched on the main queue — no atomics needed. Pairs begin/end so the
// engine's counted pause (CSoundRender_Scene::pause_emitters) never underflows.
bool g_interrupted = false;

ALCdevice* current_al_device()
{
    // xrSound makes its context current in CSoundRender_CoreA::_initialize and
    // keeps it so; before Engine.Sound.Create() (or with -nosound) this is null
    // and the ALC pause/resume is simply skipped.
    if (ALCcontext* context = alcGetCurrentContext())
        return alcGetContextsDevice(context);
    return nullptr;
}

bool activate_session(const char* who)
{
    NSError* error = nil;
    if ([[AVAudioSession sharedInstance] setActive:YES error:&error])
        return true;
    Msg("! iOS audio: %s: AVAudioSession setActive failed: %s", who,
        error ? error.localizedDescription.UTF8String : "unknown error");
    return false;
}

void begin_interruption()
{
    if (g_interrupted)
        return;
    g_interrupted = true;
    Log("iOS audio: interruption began, suspending sound");
    if (g_on_suspend)
        g_on_suspend();
    // Halt OpenAL Soft's mixer and its CoreAudio IO unit. iOS has already torn
    // the output down; leaving the ALC device 'running' is exactly what makes
    // the mute permanent, because nothing would ever start the unit again.
    if (ALCdevice* device = current_al_device())
        alcDevicePauseSOFT(device);
}

void end_interruption(const char* why)
{
    if (!g_interrupted)
        return;
    if (!activate_session(why))
        return; // e.g. the phone call has not fully ended — stay suspended;
                // the next interruption-end / did-become-active retries.
    g_interrupted = false;
    if (ALCdevice* device = current_al_device())
        alcDeviceResumeSOFT(device); // restarts the CoreAudio unit
    if (g_on_resume)
        g_on_resume();
    Msg("iOS audio: interruption ended (%s), sound resumed", why);
}
} // namespace

namespace ios_audio
{
void initialize(callback onSuspend, callback onResume)
{
    g_on_suspend = onSuspend;
    g_on_resume = onResume;

    @autoreleasepool
    {
        AVAudioSession* session = [AVAudioSession sharedInstance];

        NSError* error = nil;
        // Playback with no options: non-mixable (mixWithOthers OFF — the game
        // interrupts background music, like a desktop game taking audio focus)
        // and still audible with the ringer/silent switch engaged.
        if (![session setCategory:AVAudioSessionCategoryPlayback error:&error])
            Msg("! iOS audio: setCategory(Playback) failed: %s",
                error ? error.localizedDescription.UTF8String : "unknown error");

        if (activate_session("initialize"))
            Log("iOS audio: AVAudioSession active (Playback, non-mixable)");

        NSNotificationCenter* center = NSNotificationCenter.defaultCenter;
        NSOperationQueue* main_queue = NSOperationQueue.mainQueue;

        // NB: the notification center strongly holds block-based observers until
        // removeObserver: is called. These observations are process-lifetime, so
        // the returned tokens are deliberately dropped (also keeps this file
        // ARC/MRC-neutral).
        [center addObserverForName:AVAudioSessionInterruptionNotification
                            object:session
                             queue:main_queue
                        usingBlock:^(NSNotification* notification) {
            NSDictionary* info = notification.userInfo;
            const NSUInteger type =
                [info[AVAudioSessionInterruptionTypeKey] unsignedIntegerValue];
            if (type == AVAudioSessionInterruptionTypeBegan)
            {
                begin_interruption();
            }
            else if (type == AVAudioSessionInterruptionTypeEnded)
            {
                const NSUInteger options =
                    [info[AVAudioSessionInterruptionOptionKey] unsignedIntegerValue];
                const bool should_resume =
                    (options & AVAudioSessionInterruptionOptionShouldResume) != 0;
                // Resume when iOS says to, or whenever we are the foreground app
                // anyway — an on-screen game should always reclaim its audio.
                const bool app_active =
                    UIApplication.sharedApplication.applicationState == UIApplicationStateActive;
                if (should_resume || app_active)
                    end_interruption(should_resume ? "shouldResume" : "app active");
                else
                    Log("iOS audio: interruption ended in background without shouldResume, deferring to foreground");
            }
        }];

        // Backstop: if the interruption-end notification never carried
        // shouldResume (long phone call, Siri while backgrounded), reclaim the
        // session when we return on screen. No-ops unless g_interrupted.
        [center addObserverForName:UIApplicationDidBecomeActiveNotification
                            object:nil
                             queue:main_queue
                        usingBlock:^(NSNotification* notification) {
            (void)notification;
            end_interruption("did become active");
        }];

        // Route changes (headphones/Bluetooth attach/detach). OpenAL Soft's
        // CoreAudio backend follows the system default route by itself; log the
        // reason so device reports can correlate audio complaints with routing.
        [center addObserverForName:AVAudioSessionRouteChangeNotification
                            object:session
                             queue:main_queue
                        usingBlock:^(NSNotification* notification) {
            const NSUInteger reason =
                [notification.userInfo[AVAudioSessionRouteChangeReasonKey] unsignedIntegerValue];
            Msg("iOS audio: route change, reason %lu", (unsigned long)reason);
        }];
    }
}
} // namespace ios_audio

#endif // IOS_AUDIO_SESSION_SUPPORTED
