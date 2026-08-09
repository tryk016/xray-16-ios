#include "stdafx.h"

#include "ios_graphics_profile.h"
#include "ios_graphics_profile_policy.h"
#include "ios_display.h"

#include "../IGame_Level.h"
#include "../XR_IOConsole.h"
#include "../defines.h"
#include "../device.h"

u32 psIOSGraphicsProfile = 1;
const xr_token iosGraphicsProfileTokens[] = {
    { "Performance", 0 },
    { "Optimal", 1 },
    { "Quality", 2 },
    { nullptr, 0 },
};

namespace
{
using ios_graphics::AdaptivePolicy;
using ios_graphics::Tier;

enum class Profile : u32
{
    Performance = 0,
    Optimal = 1,
    Quality = 2,
};

struct TierSettings
{
    float visibility;
    float geometryLod;
    float localShadowQuality;
    float shadowedLightFade;
};

constexpr TierSettings TierValues[] = {
    { 0.75f, 0.50f, 0.50f, 0.35f },
    { 0.90f, 0.75f, 0.75f, 0.425f },
    { 1.00f, 1.00f, 1.00f, 0.50f },
};

constexpr pcstr ProfileNames[] = { "Performance", "Optimal", "Quality" };
constexpr pcstr TierNames[] = { "performance", "balanced", "quality" };

AdaptivePolicy adaptivePolicy;
u32 appliedProfile = u32(-1);
Tier appliedTier = Tier::Balanced;
bool constrained = false;

Profile SelectedProfile()
{
    if (psIOSGraphicsProfile > static_cast<u32>(Profile::Quality))
        psIOSGraphicsProfile = static_cast<u32>(Profile::Optimal);
    return static_cast<Profile>(psIOSGraphicsProfile);
}

void ExecuteFloat(const pcstr command, const float value)
{
    string128 line;
    xr_sprintf(line, "%s %.3f", command, value);
    Console->Execute(line);
}

void ApplyTier(const Profile profile, const Tier tier, const int fpsLimit, const pcstr reason)
{
    const TierSettings& values = TierValues[static_cast<unsigned>(tier)];
    ExecuteFloat("rs_vis_distance", values.visibility);
    ExecuteFloat("r__geometry_lod", values.geometryLod);
    ExecuteFloat("r2_ls_squality", values.localShadowQuality);
    ExecuteFloat("r2_slight_fade", values.shadowedLightFade);

    string64 fpsCommand;
    xr_sprintf(fpsCommand, "rs_fps_limit %d", fpsLimit);
    Console->Execute(fpsCommand);

    appliedTier = tier;
    Msg("* iOS graphics profile: %s, tier=%s, target=%d FPS (%s)", ProfileNames[static_cast<u32>(profile)],
        TierNames[static_cast<unsigned>(tier)], fpsLimit, reason);
}

void ApplySelectedProfile(const Profile profile)
{
    constrained = false;
    switch (profile)
    {
    case Profile::Performance:
        ApplyTier(profile, Tier::Performance, 60, "selected");
        adaptivePolicy.Reset(Tier::Performance);
        break;
    case Profile::Quality:
        ApplyTier(profile, Tier::Quality, 30, "selected");
        adaptivePolicy.Reset(Tier::Quality);
        break;
    case Profile::Optimal:
        ApplyTier(profile, Tier::Balanced, 30, "selected");
        adaptivePolicy.Reset(Tier::Balanced);
        break;
    }
    appliedProfile = static_cast<u32>(profile);
}

bool CanMeasure()
{
    return g_pGameLevel && g_pGameLevel->bReady && Device.b_is_InFocus && !Device.Paused() &&
        Device.dwPrecacheFrame == 0 && psIOSDiagnostics == 0;
}

pcstr ConstraintReason(const bool lowPowerMode, const ios_display::ThermalState thermal)
{
    if (lowPowerMode)
    {
        if (thermal == ios_display::ThermalState::Critical)
            return "low power + critical thermal";
        if (thermal == ios_display::ThermalState::Serious)
            return "low power + serious thermal";
        return "low power mode";
    }

    return thermal == ios_display::ThermalState::Critical ? "critical thermal" : "serious thermal";
}
} // namespace

void ios_graphics_profile_before_frame()
{
    const Profile profile = SelectedProfile();
    if (appliedProfile != static_cast<u32>(profile))
        ApplySelectedProfile(profile);

    if (profile != Profile::Optimal)
        return;

    const ios_display::ThermalState thermal = ios_display::thermal_state();
    const bool lowPowerMode = ios_display::low_power_mode_enabled();
    const bool mustConstrain = lowPowerMode ||
        thermal == ios_display::ThermalState::Serious || thermal == ios_display::ThermalState::Critical;

    if (mustConstrain)
    {
        if (!constrained || appliedTier != Tier::Performance)
        {
            ApplyTier(profile, Tier::Performance, 30, ConstraintReason(lowPowerMode, thermal));
            adaptivePolicy.Reset(Tier::Performance);
        }
        constrained = true;
        return;
    }

    if (constrained)
    {
        constrained = false;
        ApplyTier(profile, Tier::Balanced, 30, "constraint cleared");
        adaptivePolicy.Reset(Tier::Balanced);
        return;
    }

    if (thermal == ios_display::ThermalState::Fair && appliedTier == Tier::Quality)
    {
        ApplyTier(profile, Tier::Balanced, 30, "fair thermal cap");
        adaptivePolicy.Reset(Tier::Balanced);
    }
}

void ios_graphics_profile_on_frame(const float workMilliseconds, const float elapsedSeconds)
{
    const Profile profile = SelectedProfile();
    if (profile != Profile::Optimal || constrained)
        return;

    const ios_display::ThermalState thermal = ios_display::thermal_state();

    if (!CanMeasure())
    {
        adaptivePolicy.Suspend();
        return;
    }

    const bool allowUpgrade =
        thermal == ios_display::ThermalState::Nominal || appliedTier == Tier::Performance;
    const Tier nextTier = adaptivePolicy.Observe(workMilliseconds, elapsedSeconds, allowUpgrade);
    if (nextTier != appliedTier)
        ApplyTier(profile, nextTier, 30, nextTier < appliedTier ? "frame budget exceeded" : "sustained headroom");
}

void ios_graphics_profile_on_config_loaded()
{
    // cfg_load executes commands in file order. A saved file can set the iOS
    // profile first and then overwrite its runtime knobs with legacy desktop
    // values. Reapply synchronously after the whole file has executed: the
    // caller may issue vid_restart before the next frame boundary.
    appliedProfile = u32(-1);
    adaptivePolicy.Suspend();
    ios_graphics_profile_before_frame();
}
