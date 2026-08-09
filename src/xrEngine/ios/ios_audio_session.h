#pragma once

// iOS-only AVAudioSession lifecycle shim (audit P1: interruptions permanently
// mute OpenAL) — see ios_audio_session.mm for the full story.
//
// This header intentionally includes NO engine headers: Common/Platform.hpp pulls
// Common/PlatformApple.inl, whose `typedef int32_t BOOL` collides with the
// Objective-C runtime's BOOL, so it can never enter an Objective-C++ TU. The
// platform test below is exactly the one Common/Platform.hpp uses to define
// XR_PLATFORM_APPLE_IOS (TargetConditionals' TARGET_OS_IOS).

#if defined(__APPLE__)
#   include <TargetConditionals.h>
#   if TARGET_OS_IOS
#       define IOS_AUDIO_SESSION_SUPPORTED 1
#   endif
#endif

#if defined(IOS_AUDIO_SESSION_SUPPORTED)

namespace ios_audio
{
// Callbacks fire on the app's main thread: the observers are registered on the
// main NSOperationQueue, and on iOS SDL pumps the main runloop from inside the
// engine's frame loop (which itself must run on the main thread), so delivery is
// serialized with the game loop and callbacks may touch engine sound state.
using callback = void (*)();

// Configure the shared AVAudioSession (category Playback, non-mixable: game
// audio interrupts background apps' music, matching desktop focus behavior),
// activate it, and register interruption / route-change / foreground observers.
//
//   onSuspend — an interruption began (Siri, phone call, alarm); the engine
//               snapshots and freezes exactly its current emitter scenes.
//   onResume  — the session was reactivated after the interruption ended; the
//               engine resumes only that same snapshot.
//
// The shim itself pauses/resumes the OpenAL Soft device around the callbacks
// (ALC_SOFT_pause_device — this is what actually restarts the CoreAudio unit);
// callers only handle engine-side state. The sound manager owns the pairing so
// it remains correct when a level replaces scenes during an interruption.
//
// Call once at startup, before the OpenAL device is opened
// (i.e. before Engine.Sound.Create()).
void initialize(callback onSuspend, callback onResume);
} // namespace ios_audio

#endif // IOS_AUDIO_SESSION_SUPPORTED
