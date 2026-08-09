#include "stdafx.h"

#include "xrEngine/CustomHUD.h"
#include "xrCore/Threading/TaskManager.hpp"

#if defined(XR_PLATFORM_APPLE_IOS)
#include "ios_sector_fallback_policy.h"
#include "xrEngine/IGame_Level.h"

#include <cmath>
#include <cstdio>
#include <unistd.h>
#endif

namespace xray::render::RENDER_NAMESPACE
{
#if defined(XR_PLATFORM_APPLE_IOS)
namespace
{
struct IosSectorStartupPayload
{
    char text[512]{};
};

const char* IosSectorStartupMethodName(const IosSectorStartupMethod method)
{
    switch (method)
    {
    case IosSectorStartupMethod::Exact: return "exact";
    case IosSectorStartupMethod::Fallback: return "fallback";
    case IosSectorStartupMethod::Retained: return "retained";
    case IosSectorStartupMethod::None: return "none";
    }

    return "none";
}

bool BuildIosSectorLevelToken(char* const token, const size_t capacity)
{
    if (!g_pGameLevel || capacity < 2)
        return false;

    const pcstr source = g_pGameLevel->name().c_str();
    if (!source || !source[0])
        return false;

    size_t index = 0;
    for (; source[index]; ++index)
    {
        const char character = source[index];
        const bool allowed = (character >= 'a' && character <= 'z')
            || (character >= 'A' && character <= 'Z')
            || (character >= '0' && character <= '9')
            || character == '_' || character == '-';
        if (!allowed || index + 1 >= capacity)
            return false;

        token[index] = character;
    }

    token[index] = '\0';
    return true;
}

bool IsFiniteIosSectorPosition(const Fvector& position)
{
    return std::isfinite(position.x) && std::isfinite(position.y) && std::isfinite(position.z);
}

bool SameIosSectorPosition(const Fvector& left, const Fvector& right)
{
    return left.x == right.x && left.y == right.y && left.z == right.z;
}

bool IsIosFallbackProbe(const Fvector& camera, const Fvector& probe, const float radius)
{
    constexpr float tolerance = 0.0011f;
    bool allowedRadius = false;
    for (const float policyRadius : ios_sector_fallback::ProbeRadii)
    {
        if (radius == policyRadius)
        {
            allowedRadius = true;
            break;
        }
    }
    if (!allowedRadius)
        return false;

    for (const auto& direction : ios_sector_fallback::ProbeDirections)
    {
        const float expectedX = camera.x + direction[0] * radius;
        const float expectedZ = camera.z + direction[1] * radius;
        if (std::fabs(probe.x - expectedX) <= tolerance
            && std::fabs(probe.y - camera.y) <= tolerance
            && std::fabs(probe.z - expectedZ) <= tolerance)
            return true;
    }
    return false;
}

bool BuildIosSectorStartupPayload(
    const IosSectorStartupPendingReport& report, IosSectorStartupPayload& payload)
{
    if (!report.active || !report.transition.valid() || report.transition.epoch == 0
        || !std::isfinite(report.radius) || !IsFiniteIosSectorPosition(report.camera)
        || !IsFiniteIosSectorPosition(report.probe))
        return false;

    const pid_t process_id = getpid();
    if (process_id <= 0)
        return false;

    string64 level_token{};
    if (!BuildIosSectorLevelToken(level_token, sizeof(level_token)))
        return false;

    const bool resolved = report.transition.observation
        == ios_sector_fallback::StartupObservation::ReportResolved;
    const bool unresolved = report.transition.observation
        == ios_sector_fallback::StartupObservation::ReportUnresolved;
    const bool validSector = report.sector != IRender_Sector::INVALID_SECTOR_ID;
    const bool samePosition = SameIosSectorPosition(report.camera, report.probe);
    bool validMetadata = false;
    switch (report.method)
    {
    case IosSectorStartupMethod::Exact:
        validMetadata = resolved && validSector && samePosition && report.radius == 0.f;
        break;
    case IosSectorStartupMethod::Fallback:
        validMetadata = (resolved && validSector
                && IsIosFallbackProbe(report.camera, report.probe, report.radius))
            || (unresolved && !validSector && samePosition && report.radius == 0.f);
        break;
    case IosSectorStartupMethod::Retained:
        validMetadata = resolved && validSector && samePosition && report.radius == 0.f
            && report.transition.trigger == ios_sector_fallback::StartupTrigger::QuickLoad;
        break;
    case IosSectorStartupMethod::None:
        validMetadata = unresolved && !validSector && samePosition && report.radius == 0.f;
        break;
    }
    if (!validMetadata)
        return false;

    const pcstr status = resolved
        ? "resolved" : "unresolved";
    const int length = std::snprintf(payload.text, sizeof(payload.text),
        "* iOS sector startup v1 pid=%d epoch=%llu frame=%u level=%s trigger=%s status=%s method=%s "
        "sector=%u camera=(%.3f,%.3f,%.3f) probe=(%.3f,%.3f,%.3f) radius=%.1f",
        static_cast<int>(process_id), static_cast<unsigned long long>(report.transition.epoch), report.frame,
        level_token, ios_sector_fallback::StartupTriggerName(report.transition.trigger), status,
        IosSectorStartupMethodName(report.method), static_cast<u32>(report.sector),
        report.camera.x, report.camera.y, report.camera.z,
        report.probe.x, report.probe.y, report.probe.z, report.radius);
    return length > 0 && static_cast<size_t>(length) < sizeof(payload.text);
}

void StoreIosSectorStartupPending(IosSectorStartupPendingReport& pending,
    const ios_sector_fallback::PreparedTransition& transition, const IosSectorStartupMethod method,
    const IRender_Sector::sector_id_t sector, const Fvector& camera, const Fvector& probe,
    const float radius, const u32 frame)
{
    if (pending.active || !transition.valid())
        return;

    pending = { true, transition, method, sector, camera, probe, radius, frame };
}

void RetryIosSectorStartupPending(ios_sector_fallback::StartupEvidence& evidence,
    IosSectorStartupPendingReport& pending)
{
    if (!pending.active)
        return;

    IosSectorStartupPayload payload;
    if (!BuildIosSectorStartupPayload(pending, payload))
        return;

    const auto transition = pending.transition;
    if (!evidence.CommitPrepared(transition))
    {
        pending = {};
        return;
    }

    pending = {};
    Msg("%s", payload.text);
    FlushLog();
}
} // namespace
#endif

float g_fSCREEN;

extern float r_dtex_range;
extern float r_ssaDISCARD;
extern float r_ssaDONTSORT;
extern float r_ssaLOD_A;
extern float r_ssaLOD_B;
extern float r_ssaHZBvsTEX;
extern float r_ssaGLOD_start, r_ssaGLOD_end;

extern int ps_r2_mt_calculate;
extern int ps_r2_mt_render;


//-----
void render_main::init()
{
    o.mt_calc_enabled = RImplementation.o.mt_calculate && !RImplementation.o.oldshadowcascades && !ps_r2_ls_flags.test(R2FLAG_ZFILL);
    o.mt_draw_enabled = false; // always on imm context
    o.active = true; // always active
}

void render_main::calculate()
{
    ZoneScoped;

    auto& dsgraph_main = RImplementation.get_imm_context();

    dsgraph_main.o.phase = CRender::PHASE_NORMAL;
    dsgraph_main.r_pmask(true, true, true); // enable priority "0,1",+ capture wmarks
    if (RImplementation.r_sun.o.active && RImplementation.o.oldshadowcascades)
        dsgraph_main.set_Recorder(&RImplementation.main_coarse_structure); // this is a show-stopper. Can't be paralleled with sun
    else
        dsgraph_main.set_Recorder(nullptr);
    dsgraph_main.o.use_hom = true;
    dsgraph_main.o.is_main_pass = true;
    dsgraph_main.o.sector_id = RImplementation.last_sector_id;
    dsgraph_main.o.portal_traverse_flags =
        CPortalTraverser::VQ_HOM | CPortalTraverser::VQ_SSA | CPortalTraverser::VQ_FADE;
    dsgraph_main.o.spatial_traverse_flags = ISpatial_DB::O_ORDERED;
    dsgraph_main.o.spatial_types = STYPE_RENDERABLE | STYPE_LIGHTSOURCE;
    dsgraph_main.o.view_pos = Device.vCameraPosition;
    dsgraph_main.o.xform = Device.mFullTransform;
    dsgraph_main.o.view_frustum = RImplementation.ViewBase;
    dsgraph_main.o.query_box_side = VIEWPORT_NEAR + EPS_L;
    dsgraph_main.o.precise_portals = true;
    dsgraph_main.o.mt_calculate = o.mt_calc_enabled;

    dsgraph_main.build_subspace();
}

void render_main::render()
{
    // TODO
}

//-----

void CRender::Calculate()
{
    ZoneScopedN("r2_calculate");

    // Transfer to global space to avoid deep pointer access
    float fov_factor = _sqr(90.f / Device.fFOV);
    g_fSCREEN = float(Target->get_width(RCache) * Target->get_height(RCache)) * fov_factor * (EPS_S + ps_r__LOD);
    r_ssaDISCARD = _sqr(ps_r__ssaDISCARD) / g_fSCREEN;
    r_ssaDONTSORT = _sqr(ps_r__ssaDONTSORT / 3) / g_fSCREEN;
    r_ssaLOD_A = _sqr(ps_r2_ssaLOD_A / 3) / g_fSCREEN;
    r_ssaLOD_B = _sqr(ps_r2_ssaLOD_B / 3) / g_fSCREEN;
    r_ssaGLOD_start = _sqr(ps_r__GLOD_ssa_start / 3) / g_fSCREEN;
    r_ssaGLOD_end = _sqr(ps_r__GLOD_ssa_end / 3) / g_fSCREEN;
    r_ssaHZBvsTEX = _sqr(ps_r__ssaHZBvsTEX / 3) / g_fSCREEN;
    r_dtex_range = ps_r2_df_parallax_range * g_fSCREEN / (1024.f * 768.f);

    // Configure
    o.distortion    = o.distortion_enabled;
    o.mt_calculate  = ps_r2_mt_calculate > 0;
#ifdef USE_DX11
    o.mt_render     = ps_r2_mt_render > 0;
#else
    o.mt_render     = 0; // OpenGL does not support parallel draw calls
#endif

    if (m_bFirstFrameAfterReset)
        return;

#if defined(XR_PLATFORM_APPLE_IOS)
    RetryIosSectorStartupPending(ios_sector_startup_evidence, ios_sector_startup_pending_report);
#endif

    auto& dsgraph_main = get_imm_context();

    // Detect camera-sector
    if (!Device.vCameraPositionSaved.similar(Device.vCameraPosition, EPS_L))
    {
        auto sector_id = dsgraph_main.detect_sector(Device.vCameraPosition);
#if defined(XR_PLATFORM_APPLE_IOS)
        // Some outdoor spawn points sit above a hole in the static collision mesh.
        // The legacy detector only casts straight down/up, so it can leave the
        // camera without a sector and the main pass then draws only sky and HUD.
        // Probe the nearest surrounding floor only when the exact vertical query
        // failed; normal sector detection and all non-iOS platforms stay unchanged.
        const auto fallback = ios_sector_fallback::Resolve(Device.vCameraPosition, sector_id,
            IRender_Sector::INVALID_SECTOR_ID,
            [&dsgraph_main](const Fvector& probePosition) { return dsgraph_main.detect_sector(probePosition); });
        sector_id = fallback.sector;

        const bool committed = ios_sector_fallback::CommitValidSector(sector_id,
            IRender_Sector::INVALID_SECTOR_ID,
            last_sector_id, [](const auto sector) { g_pGamePersistent->OnSectorChanged(sector); });
        if (!ios_sector_startup_pending_report.active)
        {
            const auto prepared = ios_sector_startup_evidence.PrepareDetected(committed);
            StoreIosSectorStartupPending(ios_sector_startup_pending_report, prepared,
                fallback.fallbackAttempted ? IosSectorStartupMethod::Fallback : IosSectorStartupMethod::Exact,
                sector_id, Device.vCameraPosition, fallback.probePosition, fallback.matchedRadius, Device.dwFrame);
            RetryIosSectorStartupPending(ios_sector_startup_evidence, ios_sector_startup_pending_report);
        }

        if (fallback.fallbackAttempted)
        {
            static u32 next_success_log_frame = 0;
            static u32 next_failure_log_frame = 0;
            if (sector_id != IRender_Sector::INVALID_SECTOR_ID)
            {
                if (Device.dwFrame >= next_success_log_frame)
                {
                    next_success_log_frame = Device.dwFrame + 60;
                    Msg("* iOS sector fallback: camera=(%.2f %.2f %.2f) probe=(%.2f %.2f %.2f) "
                        "radius=%.1f sector=%u",
                        Device.vCameraPosition.x, Device.vCameraPosition.y, Device.vCameraPosition.z,
                        fallback.probePosition.x, fallback.probePosition.y, fallback.probePosition.z,
                        fallback.matchedRadius, u32(sector_id));
                }
            }
            else if (Device.dwFrame >= next_failure_log_frame)
            {
                next_failure_log_frame = Device.dwFrame + 60;
                Msg("* iOS sector fallback failed: camera=(%.2f %.2f %.2f)",
                    Device.vCameraPosition.x, Device.vCameraPosition.y, Device.vCameraPosition.z);
            }
        }
#else
        if (sector_id != IRender_Sector::INVALID_SECTOR_ID)
        {
            if (sector_id != last_sector_id)
                g_pGamePersistent->OnSectorChanged(sector_id);

            last_sector_id = sector_id;
        }
#endif
    }
#if defined(XR_PLATFORM_APPLE_IOS)
    else
    {
        const bool quickLoadEpoch = ios_sector_startup_evidence.active()
            && ios_sector_startup_evidence.trigger() == ios_sector_fallback::StartupTrigger::QuickLoad;
        const bool cameraBarrierPassed = quickLoadEpoch
            && ios_quick_load_camera_barrier.Passed(Device.ios_camera_apply_generation());
        const bool mayPrepareNoDetection = ios_sector_startup_evidence.active()
            && (!quickLoadEpoch || cameraBarrierPassed);
        if (!ios_sector_startup_pending_report.active && mayPrepareNoDetection)
        {
            const bool retainedSector = last_sector_id != IRender_Sector::INVALID_SECTOR_ID;
            const auto prepared = ios_sector_startup_evidence.PrepareNoDetection(retainedSector);
            StoreIosSectorStartupPending(ios_sector_startup_pending_report, prepared,
                retainedSector ? IosSectorStartupMethod::Retained : IosSectorStartupMethod::None,
                last_sector_id, Device.vCameraPosition, Device.vCameraPosition, 0.f, Device.dwFrame);
            if (prepared.valid() && quickLoadEpoch)
                ios_quick_load_camera_barrier.Disarm();
            RetryIosSectorStartupPending(ios_sector_startup_evidence, ios_sector_startup_pending_report);
        }
    }
#endif

    //
    Lights.Update();

    // Check if we touch some light even trough portal
    static xr_vector<ISpatial*> spatial_lights;
    g_pGamePersistent->SpatialSpace.q_sphere(spatial_lights, 0, STYPE_LIGHTSOURCE, Device.vCameraPosition, EPS_L);
    for (auto spatial : spatial_lights)
    {
        const auto& entity_pos = spatial->spatial_sector_point();
        spatial->spatial_updatesector(dsgraph_main.detect_sector(entity_pos));
        const auto sector_id = spatial->GetSpatialData().sector_id;
        if (sector_id == IRender_Sector::INVALID_SECTOR_ID)
            continue; // disassociated from S/P structure

        VERIFY(spatial->GetSpatialData().type & STYPE_LIGHTSOURCE);
        // lightsource
        light* L = (light*)spatial->dcast_Light();
        VERIFY(L);
        Lights.add_light(L);
    }

    TaskScheduler->Wait(*ProcessHOMTask);

    r_main.init();
    if (o.oldshadowcascades)
        r_sun_old.init();
    else
        r_sun.init();
#if RENDER != R_R2
    r_rain.init();
#endif

    // Main calc
    BasicStats.Culling.Begin();
    {
        r_main.run();
    }
    BasicStats.Culling.End();

    // Rain calc
#if RENDER != R_R2
    r_rain.run();
#endif

    // Sun calc
    if (o.oldshadowcascades)
        r_sun_old.run();
    else
        r_sun.run();
}
} // namespace xray::render::RENDER_NAMESPACE
