//-----------------------------------------------------------------------------
// File: x_ray.cpp
//
// Programmers:
// Oles - Oles Shishkovtsov
// AlexMX - Alexander Maksimchuk
//-----------------------------------------------------------------------------
#include "stdafx.h"

#include "x_ray.h"

#include "embedded_resources_management.h"

#include "xrCore/Threading/TaskManager.hpp"
#include "xrNetServer/NET_AuthCheck.h"

#include "IGame_Persistent.h"
#include "LightAnimLibrary.h"
#include "XR_IOConsole.h"

#if defined(XR_PLATFORM_WINDOWS)
#include "AccessibilityShortcuts.hpp"
#include "Text_Console.h"
#else
#define CTextConsole CConsole
#pragma todo("Implement text console or it's alternative")
#endif

#if defined(XR_PLATFORM_APPLE_IOS)
#include "ios/ios_audio_session.h"
#include "ios/ios_lifecycle_state.h"
#include "ios/ios_low_memory_policy.h"
#include "ios/ios_memory.h"

#include <unistd.h>
#endif

#ifdef XR_PLATFORM_WINDOWS
#include <locale>

#include "DiscordGameSDK/discord.h"
#define USE_DISCORD_INTEGRATION

#include "xrCore/Text/StringConversion.hpp"
#endif

// global variables
constexpr size_t MAX_WINDOW_EVENTS = 32;

#ifdef USE_DISCORD_INTEGRATION
constexpr discord::ClientId DISCORD_APP_ID = 421286728695939072;
#endif

ENGINE_API CInifile* pGameIni = nullptr;
ENGINE_API bool CallOfPripyatMode = false;
ENGINE_API bool ClearSkyMode = false;
ENGINE_API bool ShadowOfChernobylMode = false;

ENGINE_API string512 g_sLaunchOnExit_params{};
ENGINE_API string512 g_sLaunchOnExit_app{};
ENGINE_API string_path g_sLaunchWorkingFolder{};
extern ENGINE_API bool g_bRendering;

namespace
{
#if defined(XR_PLATFORM_APPLE_IOS)
ios_lifecycle::EventInbox iosLifecycleEvents;
ios_low_memory::Policy iosLowMemoryPolicy;

void ReportIOSLifecycleTransition(const bool foreground)
{
    const pid_t pid = getpid();
    if (pid <= 0)
        return;

    static u64 seq = 0;
    ++seq;
    Msg("* iOS lifecycle v1 pid=%d seq=%llu event=%s", static_cast<int>(pid),
        static_cast<unsigned long long>(seq), foreground ? "activate" : "deactivate");
    FlushLog();
}

void ProcessIOSLifecycleEvents()
{
    const ios_lifecycle::PendingEvents events = iosLifecycleEvents.Consume();
    const bool focusBefore = Device.b_is_InFocus;
    bool activityApplied = false;

    // Event-watch callbacks are allowed to arrive off the engine thread. Keep
    // Console and filesystem work here on the main loop, before deactivation.
    ios_lifecycle::ApplyActivityOrdered(events, focusBefore,
        []
        {
            if (Console)
                Console->Execute("cfg_save");
            FlushLog();
        },
        [&activityApplied](const bool foreground)
        {
            Device.OnWindowActivate(Device.m_sdlWnd, foreground);
            activityApplied = true;
        });

    const bool focusAfter = Device.b_is_InFocus;
    if (ios_lifecycle::ShouldReportProcessedTransition(
            activityApplied, focusBefore, events.foreground, focusAfter))
        ReportIOSLifecycleTransition(focusAfter);

    const bool foreground = iosLifecycleEvents.IsForeground() && Device.b_is_InFocus;
    const bool primaryContext = GEnv.Render
        && GEnv.Render->GetCurrentContext() == IRender::PrimaryContext;
    const ios_low_memory::Decision decision = iosLowMemoryPolicy.Update(
        events.lowMemory, foreground, Device.b_is_Ready, primaryContext, g_bRendering);

    if (decision.trimCpu)
    {
        ios_memory::Log("before_lowmemory_cpu_trim", ios_memory::Capture());
        Memory.mem_compact();
        ios_memory::Log("after_lowmemory_cpu_trim", ios_memory::Capture());
    }

    if (!decision.evictGpu)
        return;

    // Recheck persistent UIKit state and the actual renderer context directly
    // before the first GL delete. b_is_Active is deliberately not used here:
    // UIKit foreground state plus focus is the stronger lifecycle invariant.
    if (!iosLifecycleEvents.IsForeground() || !Device.b_is_InFocus || !Device.b_is_Ready
        || !GEnv.Render || GEnv.Render->GetCurrentContext() != IRender::PrimaryContext || g_bRendering)
    {
        iosLowMemoryPolicy.DeferGpuEviction();
        return;
    }

    u32 memoryBefore = 0, countBefore = 0, lightmapBefore = 0, lightmapCountBefore = 0;
    GEnv.Render->ResourcesGetMemoryUsage(memoryBefore, countBefore, lightmapBefore, lightmapCountBefore);
    const u64 totalBefore = static_cast<u64>(memoryBefore) + lightmapBefore;
    Msg("* iOS low memory: warning received; loaded texture storage %llu K "
        "(%u base and %u lightmap resources registered)",
        static_cast<unsigned long long>(totalBefore / 1024), countBefore, lightmapCountBefore);
    ios_memory::Log("before_lowmemory_eviction", ios_memory::Capture());

    u32 evictedCount = 0;
    u64 evictedBytes = 0;
    GEnv.Render->ResourcesLowMemoryEvict(evictedCount, evictedBytes);

    u32 memoryAfter = 0, countAfter = 0, lightmapAfter = 0, lightmapCountAfter = 0;
    GEnv.Render->ResourcesGetMemoryUsage(memoryAfter, countAfter, lightmapAfter, lightmapCountAfter);
    const u64 totalAfter = static_cast<u64>(memoryAfter) + lightmapAfter;
    const u64 released = totalBefore > totalAfter ? totalBefore - totalAfter : 0;
    Msg("* iOS low memory: eviction complete; loaded texture storage %llu K (released %llu K; "
        "%u surfaces / %llu K selected; %u base and %u lightmap resources registered)",
        static_cast<unsigned long long>(totalAfter / 1024),
        static_cast<unsigned long long>(released / 1024), evictedCount,
        static_cast<unsigned long long>(evictedBytes / 1024), countAfter, lightmapCountAfter);
    ios_memory::Log("after_lowmemory_eviction", ios_memory::Capture());
}
#endif

struct PathIncludePred
{
private:
    const xr_auth_strings_t* ignored;

public:
    explicit PathIncludePred(const xr_auth_strings_t* ignoredPaths) : ignored(ignoredPaths) {}
    bool IsIncluded(pcstr path)
    {
        if (!ignored)
            return true;

        return allow_to_include_path(*ignored, path);
    }
};
}

template <typename T>
void InitConfig(T& config, pcstr name, bool fatal = true,
    bool readOnly = true, bool loadAtStart = true, bool saveAtEnd = true,
    u32 sectCount = 0, const CInifile::allow_include_func_t& allowIncludeFunc = nullptr)
{
    string_path fname;
    FS.update_path(fname, "$game_config$", name);
    config = xr_new<CInifile>(fname, readOnly, loadAtStart, saveAtEnd, sectCount, allowIncludeFunc);

    CHECK_OR_EXIT(config->section_count() || !fatal,
        make_string("Cannot find file %s.\nReinstalling application may fix this problem.", fname));
}

// XXX: make it more fancy
// некрасиво слишком
void set_shoc_mode()
{
    CallOfPripyatMode = false;
    ShadowOfChernobylMode = true;
    ClearSkyMode = false;
}

void set_cs_mode()
{
    CallOfPripyatMode = false;
    ShadowOfChernobylMode = false;
    ClearSkyMode = true;
}

void set_cop_mode()
{
    CallOfPripyatMode = true;
    ShadowOfChernobylMode = false;
    ClearSkyMode = false;
}

void set_free_mode()
{
    CallOfPripyatMode = false;
    ShadowOfChernobylMode = false;
    ClearSkyMode = false;
}

void InitSettings()
{
    ZoneScoped;

    xr_auth_strings_t ignoredPaths, checkedPaths;
    fill_auth_check_params(ignoredPaths, checkedPaths); //TODO port xrNetServer to Linux
    PathIncludePred includePred(&ignoredPaths);
    CInifile::allow_include_func_t includeFilter;
    includeFilter.bind(&includePred, &PathIncludePred::IsIncluded);

    InitConfig(pSettings, "system.ltx");
    InitConfig(pSettingsAuth, "system.ltx", true, true, true, false, 0, includeFilter);
    InitConfig(pSettingsOpenXRay, "openxray.ltx", false, true, true, false);
    InitConfig(pGameIni, "game.ltx");

    if (strstr(Core.Params, "-shoc") || strstr(Core.Params, "-soc"))
        set_shoc_mode();
    else if (strstr(Core.Params, "-cs"))
        set_cs_mode();
    else if (strstr(Core.Params, "-cop"))
        set_cop_mode();
    else if (strstr(Core.Params, "-unlock_game_mode"))
        set_free_mode();
    else
    {
        pcstr gameMode = READ_IF_EXISTS(pSettingsOpenXRay, r_string, "compatibility", "game_mode", "cop");
        if (xr_strcmpi("cop", gameMode) == 0)
            set_cop_mode();
        else if (xr_strcmpi("cs", gameMode) == 0)
            set_cs_mode();
        else if (xr_strcmpi("shoc", gameMode) == 0 || xr_strcmpi("soc", gameMode) == 0)
            set_shoc_mode();
        else if (xr_strcmpi("unlock", gameMode) == 0)
            set_free_mode();
    }
}

void InitConsole()
{
    ZoneScoped;

    if (GEnv.isDedicatedServer)
        Console = xr_new<CTextConsole>();
    else
        Console = xr_new<CConsole>();

    Console->Initialize();
    xr_strcpy(Console->ConfigFile, "user.ltx");
    if (strstr(Core.Params, "-ltx "))
    {
        string64 c_name;
        sscanf(strstr(Core.Params, "-ltx ") + strlen("-ltx "), "%[^ ] ", c_name);
        xr_strcpy(Console->ConfigFile, c_name);
    }
}

void destroySettings()
{
    ZoneScoped;
    auto s = const_cast<CInifile**>(&pSettings);
    xr_delete(*s);

    auto sa = const_cast<CInifile**>(&pSettingsAuth);
    xr_delete(*sa);

    auto so = const_cast<CInifile**>(&pSettingsOpenXRay);
    xr_delete(*so);

    xr_delete(pGameIni);
}

void destroyConsole()
{
    ZoneScoped;
    Console->Execute("cfg_save");
    Console->Destroy();
    xr_delete(Console);
}

void execUserScript()
{
    ZoneScoped;
    Console->Execute("default_controls");
    Console->ExecuteScript(Console->ConfigFile);
}

constexpr pcstr FRAME_MARK_APPLICATION_STARTUP = "Application startup";
constexpr pcstr FRAME_MARK_APPLICATION_SHUTDOWN = "Application shutdown";
constexpr pcstr FRAME_MARK_APPLICATION_RUN = "Application run";

CApplication::CApplication(pcstr commandLine, GameModule* game, const std::array<RendererModule*, 2>& modules)
{
    TracySetProgramName("OpenXRay");
    Threading::SetCurrentThreadName("Primary thread");
    FrameMarkStart(FRAME_MARK_APPLICATION_STARTUP);

    if (strstr(commandLine, "-dedicated"))
        GEnv.isDedicatedServer = true;

    xrDebug::Initialize(commandLine);
    {
        ZoneScopedN("SDL_Init");
        u32 flags = SDL_INIT_VIDEO;
        if (!strstr(commandLine, "-no_gamepad"))
            flags |= SDL_INIT_GAMECONTROLLER;
        R_ASSERT3(SDL_Init(flags) == 0, "Unable to initialize SDL", SDL_GetError());
    }

#ifdef XR_PLATFORM_WINDOWS
    AccessibilityShortcuts shortcuts;
    if (!GEnv.isDedicatedServer)
        shortcuts.Disable();
#endif

#if defined(XR_PLATFORM_APPLE_IOS)
    // iOS never runs the desktop quit path (swipe-away just kills the suspended process),
    // so settings would only ever persist behind an explicit in-menu Accept. Flush them in
    // the short grace window UIKit gives when the app is being backgrounded. The watch
    // runs on the event's thread — keep it to the safe, re-entrant pieces.
    SDL_AddEventWatch(
        [](void*, SDL_Event* event) -> int
        {
            switch (event->type)
            {
            case SDL_APP_LOWMEMORY:
                iosLifecycleEvents.LowMemory();
                break;
            case SDL_APP_WILLENTERBACKGROUND:
            case SDL_APP_TERMINATING:
                // Publish inactivity and the time-critical save request as one
                // atomic transition so UIKit cannot suspend us between them.
                iosLifecycleEvents.EnterBackground(true);
                break;
            case SDL_APP_DIDENTERBACKGROUND:
                iosLifecycleEvents.EnterBackground();
                break;
            case SDL_APP_DIDENTERFOREGROUND:
                iosLifecycleEvents.EnterForeground();
                break;
            default:
                break;
            }
            return 0;
        },
        nullptr);

    // Audit P1: an AVAudioSession interruption (Siri, phone call, alarm) stops
    // the CoreAudio unit OpenAL Soft renders into and nothing restarts it, so
    // audio stays permanently muted afterwards. The shim owns session category /
    // reactivation and pauses/resumes the OpenAL device (ALC_SOFT_pause_device);
    // these callbacks freeze/thaw the engine's emitters around it. Registered
    // here, before Engine.Sound.Create(), so the session is configured before
    // alcOpenDevice. NOTE: SDL_INIT_AUDIO stays deliberately absent above — the
    // engine never uses SDL audio (OpenAL -> CoreAudio directly), and SDL's
    // coreaudio backend would set its own session category and fight this shim.
    ios_audio::initialize(
        []() // interruption began
        {
            if (GEnv.Sound)
                GEnv.Sound->begin_audio_interruption();
        },
        []() // session reactivated after the interruption
        {
            if (GEnv.Sound)
                GEnv.Sound->end_audio_interruption();
        });
#endif

    if (!strstr(commandLine, "-nosplash"))
    {
        const bool topmost = !strstr(commandLine, "-splashnotop");
        ShowSplash(topmost);
    }

    SDL_StopTextInput(); // It's enabled by default for some reason, we don't want it
    const auto& inputTask = TaskManager::AddTask([]
    {
        const bool captureInput = !strstr(Core.Params, "-i");
        pInput = xr_new<CInput>(captureInput);
    });

    const auto& createSoundDevicesList = TaskManager::AddTask([]
    {
        Engine.Sound.CreateDevicesList();
    });

    pcstr fsltx = "-fsltx ";
    string_path fsgame = "";
    if (strstr(commandLine, fsltx))
    {
        const size_t sz = xr_strlen(fsltx);
        sscanf(strstr(commandLine, fsltx) + sz, "%[^ ] ", fsgame);
    }

    Core.Initialize("OpenXRay", commandLine, true, *fsgame ? fsgame : nullptr);

    InitSettings();
    // Adjust player & computer name for Asian
    if (pSettings->line_exist("string_table", "no_native_input"))
    {
        xr_strcpy(Core.UserName, sizeof(Core.UserName), "Player");
        xr_strcpy(Core.CompName, sizeof(Core.CompName), "Computer");
    }

    Device.InitializeImGui();
    Device.FillVideoModes();
    TaskScheduler->Wait(inputTask);
    InitConsole();

    Engine.Initialize(game, modules);
    Device.Initialize();

    Console->OnDeviceInitialize();

    execUserScript();
    InitializeDiscord();

    TaskScheduler->Wait(createSoundDevicesList);
    Engine.Sound.Create();

    // ...command line for auto start
    pcstr startArgs = strstr(Core.Params, "-start ");
    if (startArgs)
        Console->Execute(startArgs + 1);
    pcstr loadArgs = strstr(Core.Params, "-load ");
    if (loadArgs)
        Console->Execute(loadArgs + 1);

    // Initialize APP
    const auto& createLightAnim = TaskScheduler->AddTask([]
    {
        LALib.OnCreate();
    });

    Device.Create();
    TaskScheduler->Wait(createLightAnim);

    if (game)
    {
        m_game_module = game;
        g_pGamePersistent = game->create_persistent();
        R_ASSERT(g_pGamePersistent);
    }
    if (g_pGamePersistent)
        g_pGamePersistent->OnAppStart();
    else
        Console->Show();

    FrameMarkEnd(FRAME_MARK_APPLICATION_STARTUP);
}

CApplication::~CApplication()
{
    FrameMarkStart(FRAME_MARK_APPLICATION_SHUTDOWN);

    if (g_pGamePersistent)
        g_pGamePersistent->OnAppEnd();

    if (m_game_module)
        m_game_module->destroy_persistent(g_pGamePersistent);

    Engine.Event.Dump();

    xr_delete(pInput);
    destroySettings();

    LALib.OnDestroy();

    destroyConsole();

    Device.CleanupVideoModes();
    Device.DestroyImGui();
    Engine.Sound.Destroy();

    Device.Destroy();
    Engine.Destroy();

#ifdef USE_DISCORD_INTEGRATION
    discord::Core::Destroy(&m_discord_core);
#endif

    // check for need to execute something external
    if (/*xr_strlen(g_sLaunchOnExit_params) && */ xr_strlen(g_sLaunchOnExit_app))
    {
#if defined(XR_PLATFORM_WINDOWS)
        // CreateProcess need to return results to next two structures
        STARTUPINFO si = {};
        si.cb = sizeof(si);
        PROCESS_INFORMATION pi = {};
        // We use CreateProcess to setup working folder
        pcstr tempDir = xr_strlen(g_sLaunchWorkingFolder) ? g_sLaunchWorkingFolder : nullptr;
        CreateProcess(g_sLaunchOnExit_app, g_sLaunchOnExit_params, nullptr, nullptr, FALSE, 0, nullptr, tempDir, &si, &pi);
#endif
    }

    Core._destroy();
    {
        ZoneScopedN("SDL_Quit");
        SDL_Quit();
    }

    xrDebug::Finalize();
    FrameMarkEnd(FRAME_MARK_APPLICATION_SHUTDOWN);
}

int CApplication::Run()
{
    HideSplash();
    Device.Run();

    while (!SDL_QuitRequested()) // SDL_PumpEvents is here
    {
        FrameMarkStart(FRAME_MARK_APPLICATION_RUN);
        bool canCallActivate = false;
        bool shouldActivate = false;

#if defined(XR_PLATFORM_APPLE_IOS)
        // SDL_PumpEvents may queue FOCUS_LOST/MINIMIZED before the app-level
        // WILLENTERBACKGROUND event. The event watch has already published the
        // atomic inbox state, so drain it before window events can deactivate
        // the engine and preserve cfg_save -> deactivate ordering.
        ProcessIOSLifecycleEvents();
#endif

        SDL_Event events[MAX_WINDOW_EVENTS];
        const int count = SDL_PeepEvents(events, MAX_WINDOW_EVENTS,
            SDL_GETEVENT, SDL_WINDOWEVENT, SDL_WINDOWEVENT);

        for (int i = 0; i < count; ++i)
        {
            const SDL_Event event = events[i];

            switch (event.type)
            {
            case SDL_WINDOWEVENT:
            {
                const auto window = SDL_GetWindowFromID(event.window.windowID);

                switch (event.window.event)
                {
                case SDL_WINDOWEVENT_SHOWN:
                case SDL_WINDOWEVENT_FOCUS_GAINED:
                case SDL_WINDOWEVENT_RESTORED:
                case SDL_WINDOWEVENT_MAXIMIZED:
                    if (window != Device.m_sdlWnd)
                        Device.OnWindowActivate(window, true);
                    else
                    {
                        canCallActivate = true;
                        shouldActivate = true;
                    }
                    continue;

                case SDL_WINDOWEVENT_HIDDEN:
                case SDL_WINDOWEVENT_FOCUS_LOST:
                case SDL_WINDOWEVENT_MINIMIZED:
                    if (window != Device.m_sdlWnd)
                        Device.OnWindowActivate(window, false);
                    else
                    {
                        canCallActivate = true;
                        shouldActivate = false;
                    }
                    continue;
                } // switch (event.window.event)
            }
            } // switch (event.type)

            // Only process event in Device
            // if it wasn't processed in the switch above
            Device.ProcessEvent(event);
        } // for (int i = 0; i < count; ++i)

        // Workaround for screen blinking when there's too much timeouts
        if (canCallActivate)
        {
            Device.OnWindowActivate(Device.m_sdlWnd, shouldActivate);
        }

#if defined(XR_PLATFORM_APPLE_IOS)
        ProcessIOSLifecycleEvents();
        if (iosLifecycleEvents.TryBeginFrame())
        {
            Device.ProcessFrame();
            iosLifecycleEvents.EndFrame();
        }
        else
        {
            // A lifecycle or LOWMEMORY publication won the frame-boundary
            // race. Drain that exact snapshot and skip this frame. ProcessFrame
            // normally owns frame pacing, so retain an explicit backoff here;
            // otherwise the background loop busy-spins until UIKit suspends it.
            ios_lifecycle::HandleSkippedFrame(
                [] { ProcessIOSLifecycleEvents(); },
                [](const int milliseconds) { Sleep(milliseconds); });
        }
#else
        Device.ProcessFrame();
#endif

        UpdateDiscordStatus();
        FrameMarkEnd(FRAME_MARK_APPLICATION_RUN);
    } // while (!SDL_QuitRequested())

    Device.Shutdown();

    return 0;
}

void CApplication::ShowSplash(bool topmost)
{
    if (m_window)
        return;

    ZoneScoped;

    m_surface = std::move(ExtractSplashScreen());
    if (!m_surface)
    {
        Log("~ Couldn't create surface from image:", SDL_GetError());
        return;
    }

    Uint32 flags = SDL_WINDOW_BORDERLESS | SDL_WINDOW_HIDDEN;

    if (topmost)
        flags |= SDL_WINDOW_ALWAYS_ON_TOP;

    m_window = SDL_CreateWindow("OpenXRay", SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED, m_surface->w, m_surface->h, flags);
    SDL_ShowWindow(m_window);

    m_splash_thread = Threading::RunThread("Splash Thread", &CApplication::SplashProc, this);
    SDL_PumpEvents();
}

constexpr u32 SPLASH_FRAMERATE = 30;

void CApplication::SplashProc()
{
    {
        ZoneScopedN("Update splash image");
        const auto current = SDL_GetWindowSurface(m_window);
        SDL_BlitSurface(m_surface, nullptr, current, nullptr);
        SDL_UpdateWindowSurface(m_window);
    }
    while (!m_should_exit.load(std::memory_order_acquire))
    {
        UpdateDiscordStatus();
        Sleep(SPLASH_FRAMERATE);
    }
}

void CApplication::HideSplash()
{
    if (!m_window)
        return;

    ZoneScoped;

    m_should_exit.store(true, std::memory_order_release);
    m_splash_thread.join();

    SDL_DestroyWindow(m_window);
    m_window = nullptr;

    SDL_FreeSurface(m_surface);
}

void CApplication::InitializeDiscord()
{
#ifdef USE_DISCORD_INTEGRATION
    ZoneScoped;
    discord::Core* core;
    discord::Core::Create(DISCORD_APP_ID, discord::CreateFlags::NoRequireDiscord, &core);

#   ifndef MASTER_GOLD
    if (core)
    {
        const auto level = xrDebug::DebuggerIsPresent() ? discord::LogLevel::Debug : discord::LogLevel::Info;
        core->SetLogHook(level, [](discord::LogLevel level, pcstr message)
        {
            switch (level)
            {
            case discord::LogLevel::Error: Log("!", message); break;
            case discord::LogLevel::Warn:  Log("~", message); break;
            case discord::LogLevel::Info:  Log("*", message); break;
            case discord::LogLevel::Debug: Log("#", message); break;
            }
        });
    }
#   endif

    if (core)
    {
        const std::locale locale("");

        discord::Activity activity{};
        activity.SetType(discord::ActivityType::Playing);
        activity.SetApplicationId(DISCORD_APP_ID);
        activity.SetState(StringToUTF8(Core.ApplicationTitle, locale).c_str());
        activity.GetAssets().SetLargeImage("logo");
        core->ActivityManager().UpdateActivity(activity, nullptr);

        std::lock_guard guard{ m_discord_lock };
        m_discord_core = core;
    }
#endif
}

void CApplication::UpdateDiscordStatus()
{
#ifdef USE_DISCORD_INTEGRATION
    if (!m_discord_core)
        return;

    ZoneScoped;
    std::lock_guard guard{ m_discord_lock };
    m_discord_core->RunCallbacks();
#endif
}
