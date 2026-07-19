#include "stdafx.h"
#include "xrEngine/IGame_Persistent.h"
#include "xrEngine/Environment.h"
#include "Layers/xrRender/dxEnvironmentRender.h"

#if defined(XR_PLATFORM_APPLE_IOS)
// iOS Track A diagnostic (white world at spawn) - std::ldexp for half->float decode.
#include <cmath>
#endif

#define STENCIL_CULL 0

namespace xray::render::RENDER_NAMESPACE
{
float hclip(float v, float dim) { return 2.f * v / dim - 1.f; }

#if defined(XR_PLATFORM_APPLE_IOS)
// ===========================================================================
// iOS Track A diagnostic: "white world at spawn" timeline.
// Active only for the first 25 s of gameplay after each level load, ~1 sample/s.
// Pure read-only instrumentation - nothing here changes rendering state.
// ===========================================================================
namespace
{
// GL enums spelled numerically so this diagnostic cannot break on whichever glad
// header variant the ES build ends up with (the build loads ES entry points via
// gladLoadGLES2 while compiling against glad/gl.h).
constexpr GLenum IOSDBG_READ_FRAMEBUFFER = 0x8CA8; // GL_READ_FRAMEBUFFER
constexpr GLenum IOSDBG_READ_FRAMEBUFFER_BINDING = 0x8CAA; // GL_READ_FRAMEBUFFER_BINDING
constexpr GLenum IOSDBG_IMPL_COLOR_READ_FORMAT = 0x8B9B; // GL_IMPLEMENTATION_COLOR_READ_FORMAT
constexpr GLenum IOSDBG_IMPL_COLOR_READ_TYPE = 0x8B9A; // GL_IMPLEMENTATION_COLOR_READ_TYPE
constexpr GLint IOSDBG_HALF_FLOAT = 0x140B; // GL_HALF_FLOAT
constexpr GLint IOSDBG_HALF_FLOAT_OES = 0x8D61; // GL_HALF_FLOAT_OES
constexpr GLint IOSDBG_RED = 0x1903; // GL_RED
constexpr GLint IOSDBG_RGBA = 0x1908; // GL_RGBA
constexpr GLint IOSDBG_UNSIGNED_BYTE = 0x1401; // GL_UNSIGNED_BYTE
constexpr GLint IOSDBG_FLOAT = 0x1406; // GL_FLOAT

float iosdbg_half_to_float(u16 h)
{
    const u32 sign = (h >> 15) & 1u;
    const u32 expo = (h >> 10) & 0x1fu;
    const u32 mant = h & 0x3ffu;
    float v;
    if (expo == 0u)
        v = std::ldexp(static_cast<float>(mant), -24); // subnormal
    else if (expo == 31u)
        v = mant ? 0.f : 1.0e30f; // NaN -> 0, Inf -> big finite (keeps %f readable)
    else
        v = std::ldexp(static_cast<float>(mant + 1024u), static_cast<int>(expo) - 25);
    return sign ? -v : v;
}

// Reads one texel from a GL texture through a PRIVATE fbo that is bound to
// GL_READ_FRAMEBUFFER only. CBackend caches the GL_FRAMEBUFFER binding in
// CBackend::pFB and the attachments in pRT[]/pZB; touching only the read binding -
// and restoring it - leaves those caches valid.
// Return: 0 ok, 1 no texture, 2 fbo incomplete, 3 unreadable format / GL error.
int iosdbg_read_texel(GLuint tex, GLint x, GLint y, float out[4], GLint& rfmt, GLint& rtype)
{
    static GLuint s_fbo = 0;

    out[0] = out[1] = out[2] = out[3] = 0.f;
    rfmt = 0;
    rtype = 0;

    if (!tex)
        return 1;

    if (!s_fbo)
        glGenFramebuffers(1, &s_fbo);

    GLint prev_read = 0;
    glGetIntegerv(IOSDBG_READ_FRAMEBUFFER_BINDING, &prev_read);
    glBindFramebuffer(IOSDBG_READ_FRAMEBUFFER, s_fbo);
    glFramebufferTexture2D(IOSDBG_READ_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, tex, 0);
    glReadBuffer(GL_COLOR_ATTACHMENT0);

    int status = 3;
    if (glCheckFramebufferStatus(IOSDBG_READ_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE)
    {
        status = 2;
    }
    else
    {
        // ES is far stricter than desktop GL: only GL_RGBA/GL_UNSIGNED_BYTE (for
        // normalized fixed-point attachments) and the implementation-defined pair are
        // guaranteed. Query the pair for THIS read framebuffer and honour it.
        GLint pack = 4;
        glGetIntegerv(IOSDBG_IMPL_COLOR_READ_FORMAT, &rfmt);
        glGetIntegerv(IOSDBG_IMPL_COLOR_READ_TYPE, &rtype);
        glGetIntegerv(GL_PACK_ALIGNMENT, &pack);
        glPixelStorei(GL_PACK_ALIGNMENT, 1);
        while (glGetError() != GL_NO_ERROR) {} // drain anything we inherited

        if (rfmt == IOSDBG_RGBA && rtype == IOSDBG_UNSIGNED_BYTE)
        {
            u8 px[4] = {};
            glReadPixels(x, y, 1, 1, static_cast<GLenum>(rfmt), static_cast<GLenum>(rtype), px);
            if (glGetError() == GL_NO_ERROR)
            {
                for (int i = 0; i < 4; ++i)
                    out[i] = static_cast<float>(px[i]) / 255.f;
                status = 0;
            }
        }
        else if (rfmt == IOSDBG_RGBA && (rtype == IOSDBG_HALF_FLOAT || rtype == IOSDBG_HALF_FLOAT_OES))
        {
            u16 px[4] = {};
            glReadPixels(x, y, 1, 1, static_cast<GLenum>(rfmt), static_cast<GLenum>(rtype), px);
            if (glGetError() == GL_NO_ERROR)
            {
                for (int i = 0; i < 4; ++i)
                    out[i] = iosdbg_half_to_float(px[i]);
                status = 0;
            }
        }
        else if (rtype == IOSDBG_FLOAT && (rfmt == IOSDBG_RGBA || rfmt == IOSDBG_RED))
        {
            float px[4] = {};
            glReadPixels(x, y, 1, 1, static_cast<GLenum>(rfmt), static_cast<GLenum>(rtype), px);
            if (glGetError() == GL_NO_ERROR)
            {
                const int n = (rfmt == IOSDBG_RGBA) ? 4 : 1;
                for (int i = 0; i < n; ++i)
                    out[i] = px[i];
                status = 0;
            }
        }
        // anything else: leave status == 3, do NOT guess a format (that is how you get
        // a GL_INVALID_OPERATION storm on ES).

        glPixelStorei(GL_PACK_ALIGNMENT, pack);
    }

    glFramebufferTexture2D(IOSDBG_READ_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, 0, 0);
    glBindFramebuffer(IOSDBG_READ_FRAMEBUFFER, static_cast<GLuint>(prev_read));
    while (glGetError() != GL_NO_ERROR) {} // never leak an error into CHK_GL callers
    return status;
}

// True at most once per second, and only during the first 25 s after a level load.
// Uses dwTimeContinual (wall clock minus app-inactive time) rather than dwTimeGlobal,
// so the cadence survives exactly the pause we are hunting.
bool iosdbg_should_sample(float& t_since_start)
{
    static bool s_armed = false;
    static u32 s_start = 0;
    static u32 s_next = 0;

    if (Device.dwPrecacheFrame > 0) // (re)loading a level - re-arm for the next gameplay frame
    {
        s_armed = false;
        return false;
    }

    const u32 now = Device.dwTimeContinual;
    if (!s_armed)
    {
        s_armed = true;
        s_start = now;
        s_next = now;
    }

    const u32 elapsed = now - s_start;
    if (elapsed > 25000)
        return false;
    if (static_cast<s32>(now - s_next) < 0)
        return false;

    s_next = now + 1000;
    t_since_start = static_cast<float>(elapsed) / 1000.f;
    return true;
}
} // namespace
#endif // XR_PLATFORM_APPLE_IOS

void CRenderTarget::phase_combine()
{
    PIX_EVENT(phase_combine);

    //	TODO: DX11: Remove half pixel offset
    bool _menu_pp = g_pGamePersistent ? g_pGamePersistent->OnRenderPPUI_query() : false;

    u32 Offset = 0;
    Fvector2 p0, p1;

    //*** exposure-pipeline
    u32 gpu_id = Device.dwFrame % HW.Caps.iGPUNum;
    {
        t_LUM_src->surface_set(GL_TEXTURE_2D, rt_LUM_pool[gpu_id * 2 + 0]->pRT);
        t_LUM_dest->surface_set(GL_TEXTURE_2D, rt_LUM_pool[gpu_id * 2 + 1]->pRT);
    }

    if (RImplementation.o.ssao_hdao)
    {
        //phase_downsamp();
        //phase_ssao();
    }
    else
    {
        if (RImplementation.o.ssao_opt_data)
        {
            phase_downsamp();
            // phase_ssao();
        }
        else if (RImplementation.o.ssao_blur_on)
        {
            phase_ssao();
        }
    }

    // low/hi RTs
    {
        // Clear to zero
        RCache.ClearRT(rt_Generic_0_r, {});
        RCache.ClearRT(rt_Generic_1_r, {});
        u_setrt(RCache, rt_Generic_0_r, rt_Generic_1_r, nullptr, rt_MSAADepth);
    }
    RCache.set_CullMode(CULL_NONE);
    RCache.set_Stencil(FALSE);

    //bool split_the_scene_to_minimize_wait = ps_r2_ls_flags.test(R2FLAG_EXP_SPLIT_SCENE);

    // draw skybox
    if (1)
    {
        //	Moved to shader!
        // RCache.set_ColorWriteEnable					();
        //	Moved to shader!
        // RCache.set_Z(FALSE);
        g_pGamePersistent->Environment().RenderSky();

        //	Igor: Render clouds before compine without Z-test
        //	to avoid siluets. HOwever, it's a bit slower process.
        g_pGamePersistent->Environment().RenderClouds();

        //	Moved to shader!
        // RCache.set_Z(TRUE);
    }

    // if (RImplementation.o.bug)	{
    RCache.set_Stencil(TRUE, D3DCMP_LESSEQUAL, 0x01, 0xff, 0x00); // stencil should be >= 1
    if (RImplementation.o.nvstencil)
    {
        u_stencil_optimize(RCache, CRenderTarget::SO_Combine);
        RCache.set_ColorWriteEnable();
    }
    //}

    // calc m-blur matrices
    Fmatrix m_previous, m_current;
    Fvector2 m_blur_scale;
    {
        static Fmatrix m_saved_viewproj;

        // (new-camera) -> (world) -> (old_viewproj)
        m_previous.mul(m_saved_viewproj, Device.mInvView);
        m_current.set(Device.mProject);
        m_saved_viewproj.set(Device.mFullTransform);
        float scale = ps_r2_mblur / 2.f;
        m_blur_scale.set(scale, -scale).div(12.f);
    }

    // Draw full-screen quad textured with our scene image
    if (!_menu_pp)
    {
        PIX_EVENT(combine_1);
        // Compute params
        const auto& envdesc = g_pGamePersistent->Environment().CurrentEnv;
        const float minamb = 0.001f;
        Fvector4 ambclr =
        {
            std::max(envdesc.ambient.x * 2.f, minamb),
            std::max(envdesc.ambient.y * 2.f, minamb),
            std::max(envdesc.ambient.z * 2.f, minamb),
            0
        };
        ambclr.mul(ps_r2_sun_lumscale_amb);

        Fvector4 envclr = envdesc.env_color;
        envclr.x *= 2 * ps_r2_sun_lumscale_hemi;
        envclr.y *= 2 * ps_r2_sun_lumscale_hemi;
        envclr.z *= 2 * ps_r2_sun_lumscale_hemi;

        Fvector4 fogclr = {envdesc.fog_color.x, envdesc.fog_color.y, envdesc.fog_color.z, 0};
        Fvector4 sunclr, sundir;

        float fSSAONoise = 2.0f;
        fSSAONoise *= tan(deg2rad(67.5f / 2.0f));
        fSSAONoise /= tan(deg2rad(Device.fFOV / 2.0f));

        float fSSAOKernelSize = 150.0f;
        fSSAOKernelSize *= tan(deg2rad(67.5f / 2.0f));
        fSSAOKernelSize /= tan(deg2rad(Device.fFOV / 2.0f));

        // sun-params
        {
            light* fuckingsun = (light*)RImplementation.Lights.sun._get();
            Fvector L_dir, L_clr;
            float L_spec;
            L_clr.set(fuckingsun->color.r, fuckingsun->color.g, fuckingsun->color.b);
            L_spec = u_diffuse2s(L_clr);
            Device.mView.transform_dir(L_dir, fuckingsun->direction);
            L_dir.normalize();

            sunclr.set(L_clr.x, L_clr.y, L_clr.z, L_spec);
            sundir.set(L_dir.x, L_dir.y, L_dir.z, 0);
        }

        /*
        // Fill VB
        //float	_w					= float(Device.dwWidth);
        //float	_h					= float(Device.dwHeight);
        //p0.set						(.5f/_w, .5f/_h);
        //p1.set						((_w+.5f)/_w, (_h+.5f)/_h );
        //p0.set						(.5f/_w, .5f/_h);
        //p1.set						((_w+.5f)/_w, (_h+.5f)/_h );

        // Fill vertex buffer
        Fvector4* pv				= (Fvector4*)	RImplementation.Vertex.Lock	(4,g_combine_VP->vb_stride,Offset);
        //pv->set						(hclip(EPS,		_w),	hclip(_h+EPS,	_h),	p0.x, p1.y);	pv++;
        //pv->set						(hclip(EPS,		_w),	hclip(EPS,		_h),	p0.x, p0.y);	pv++;
        //pv->set						(hclip(_w+EPS,	_w),	hclip(_h+EPS,	_h),	p1.x, p1.y);	pv++;
        //pv->set						(hclip(_w+EPS,	_w),	hclip(EPS,		_h),	p1.x, p0.y);	pv++;
        pv->set						(-1,	1,	0, 1);	pv++;
        pv->set						(-1,	-1,	0, 0);	pv++;
        pv->set						(1,		1,	1, 1);	pv++;
        pv->set						(1,		-1,	1, 0);	pv++;
        RImplementation.Vertex.Unlock		(4,g_combine_VP->vb_stride);
        */

        // Fill VB
        float scale_X = float(Device.dwWidth) / float(TEX_jitter);
        float scale_Y = float(Device.dwHeight) / float(TEX_jitter);

        // Fill vertex buffer
        FVF::TL* pv = (FVF::TL*)RImplementation.Vertex.Lock(4, g_combine->vb_stride, Offset);
        pv->set(-1, 1, 0, 1, 0, 0, scale_Y);
        pv++;
        pv->set(-1, -1, 0, 0, 0, 0, 0);
        pv++;
        pv->set(1, 1, 1, 1, 0, scale_X, scale_Y);
        pv++;
        pv->set(1, -1, 1, 0, 0, scale_X, 0);
        pv++;
        RImplementation.Vertex.Unlock(4, g_combine->vb_stride);

        // Draw
        if (!RImplementation.o.msaa)
            RCache.set_Element(s_combine->E[0]);
        else
            RCache.set_Element(s_combine_msaa[0]->E[0]);
        RCache.set_Geometry(g_combine);

        RCache.set_c("m_v2w", Device.mInvView);
        RCache.set_c("L_ambient", ambclr);

        RCache.set_c("Ldynamic_color", sunclr);
        RCache.set_c("Ldynamic_dir", sundir);

        RCache.set_c("env_color", envclr);
        RCache.set_c("fog_color", fogclr);

        RCache.set_c("ssao_noise_tile_factor", fSSAONoise);
        RCache.set_c("ssao_kernel_size", fSSAOKernelSize);

        if (!RImplementation.o.msaa)
            RCache.Render(D3DPT_TRIANGLELIST, Offset, 0, 4, 0, 2);
        else
        {
            if (RImplementation.o.msaa_opt)
            {
                RCache.set_Stencil(TRUE, D3DCMP_EQUAL, 0x81, 0x81, 0);
                RCache.Render(D3DPT_TRIANGLELIST, Offset, 0, 4, 0, 2);
            }
            else
            {
                VERIFY(!"Only optimized MSAA is supported in OpenGL");
            }
            RCache.set_Stencil(FALSE, D3DCMP_EQUAL, 0x01, 0xff, 0);
        }

#if defined(XR_PLATFORM_APPLE_IOS)
        // ---- iOS Track A diagnostic: one timeline row, ~1/s, first 25 s after load ----
        // This is the only point in the frame where all four probes are simultaneously
        // valid: rt_Color still holds the g-buffer albedo (it is not reused as the LDR
        // PP target until later in this function), rt_Accumulator still holds the light
        // accumulation, rt_Generic_0 holds the combine result before forward geometry /
        // PP-UI overdraw it, and rt_LUM_pool[gpu_id*2+0] is the exposure texel this
        // frame's tonemapping actually consumed.
        // MSAA is excluded: multisample attachments are not readable this way.
        float iosdbg_t = 0.f;
        if (!RImplementation.o.msaa && iosdbg_should_sample(iosdbg_t))
        {
            const GLint W = static_cast<GLint>(rt_Generic_0->dwWidth);
            const GLint H = static_cast<GLint>(rt_Generic_0->dwHeight);
            const GLint gx = W / 2, gy = H / 4; // glReadPixels origin is bottom-left: lower quarter = ground
            const GLint sx = W / 2, sy = (H * 3) / 4; // upper quarter = sky reference / orientation check

            float alb[4], acc[4], fin[4], sky[4], lum[4];
            GLint f0, t0, f1, t1, f2, t2, f3, t3, f4, t4;
            const int s0 = iosdbg_read_texel(rt_Color->pRT, gx, gy, alb, f0, t0);
            const int s1 = iosdbg_read_texel(rt_Accumulator->pRT, gx, gy, acc, f1, t1);
            const int s2 = iosdbg_read_texel(rt_Generic_0->pRT, gx, gy, fin, f2, t2);
            const int s3 = iosdbg_read_texel(rt_Generic_0->pRT, sx, sy, sky, f3, t3);
            const int s4 = iosdbg_read_texel(rt_LUM_pool[gpu_id * 2 + 0]->pRT, 0, 0, lum, f4, t4);

            auto& env = g_pGamePersistent->Environment();
            u32 hh = 0, mm = 0, ss = 0;
            env.GetGameTime(hh, mm, ss);

            Msg("* iOS TrackA t=%5.2f fr=%u pf=%u intro=%d dt=%.4f dtr=%.4f gt=%02u:%02u:%02u | "
                "alb=(%.3f %.3f %.3f %.3f) acc=(%.3f %.3f %.3f %.3f) fin=(%.3f %.3f %.3f) sky=(%.3f %.3f %.3f) | "
                "lum=%.5f adapt=%.4f | sun=(%.3f %.3f %.3f) hemi=(%.3f %.3f %.3f) amb=(%.3f %.3f %.3f) | "
                "fog=(%.3f %.3f %.3f) fd=%.4f fn=%.1f ff=%.1f | w=%.3f mp=%.3f wfx=%d st=%d%d%d%d%d",
                iosdbg_t, Device.dwFrame, Device.dwPrecacheFrame, g_ios_intro_active,
                Device.fTimeDelta, Device.fTimeDeltaReal, hh, mm, ss,
                alb[0], alb[1], alb[2], alb[3], acc[0], acc[1], acc[2], acc[3],
                fin[0], fin[1], fin[2], sky[0], sky[1], sky[2],
                lum[0], f_luminance_adapt,
                sunclr.x, sunclr.y, sunclr.z, envclr.x, envclr.y, envclr.z,
                ambclr.x, ambclr.y, ambclr.z,
                fogclr.x, fogclr.y, fogclr.z, envdesc.fog_density, envdesc.fog_near, envdesc.fog_far,
                envdesc.weight, envdesc.modif_power, env.bWFX ? 1 : 0,
                s0, s1, s2, s3, s4);

            // If any probe was unreadable, report the queried ES read format/type ONCE so
            // we know which target to drop instead of spamming the log every second.
            static bool s_reported = false;
            if (!s_reported && (s0 | s1 | s2 | s3 | s4))
            {
                s_reported = true;
                Msg("! iOS TrackA probe unreadable: alb(st=%d f=%04x t=%04x) acc(st=%d f=%04x t=%04x) "
                    "fin(st=%d f=%04x t=%04x) sky(st=%d f=%04x t=%04x) lum(st=%d f=%04x t=%04x)",
                    s0, (unsigned)f0, (unsigned)t0, s1, (unsigned)f1, (unsigned)t1,
                    s2, (unsigned)f2, (unsigned)t2, s3, (unsigned)f3, (unsigned)t3,
                    s4, (unsigned)f4, (unsigned)t4);
            }
        }
#endif // XR_PLATFORM_APPLE_IOS
    }

    // Forward rendering
    {
        PIX_EVENT(Forward_rendering);
        u_setrt(RCache, rt_Generic_0_r, nullptr, nullptr, rt_MSAADepth); // LDR RT
        RCache.set_CullMode(CULL_CCW);
        RCache.set_Stencil(FALSE);
        RCache.set_ColorWriteEnable();
        //	TODO: DX11: CHeck this!
        // g_pGamePersistent->Environment().RenderClouds	();
        RImplementation.render_forward();
        if (g_pGamePersistent)
            g_pGamePersistent->OnRenderPPUI_main(); // PP-UI
    }

    //	Igor: for volumetric lights
    //	combine light volume here
    if (m_bHasActiveVolumetric)
        phase_combine_volumetric();

    // Perform blooming filter and distortion if needed
    RCache.set_Stencil(FALSE);

    if (RImplementation.o.msaa)
    {
        // we need to resolve rt_Generic_1_r into rt_Generic_1
        rt_Generic_0_r->resolve_into(*rt_Generic_0);
        rt_Generic_1_r->resolve_into(*rt_Generic_1);
    }

    // for msaa we need a resolved color buffer - Holger
    phase_bloom(); // HDR RT invalidated here

    // RImplementation.rmNormal();
    // u_setrt(rt_Generic_1,0,0,get_base_zb());

    // Distortion filter
    auto& dsgraph = RImplementation.get_imm_context();
    BOOL bDistort = RImplementation.o.distortion_enabled; // This can be modified
    {
        if ((0 == dsgraph.mapDistort.size()) && !_menu_pp)
            bDistort = FALSE;
        if (bDistort)
        {
            PIX_EVENT(render_distort_objects);
            u_setrt(RCache, rt_Generic_1_r, nullptr, nullptr, rt_MSAADepth); // Now RT is a distortion mask
            RCache.ClearRT(rt_Generic_1_r, color_rgba(127, 127, 0, 127));
            RCache.set_CullMode(CULL_CCW);
            RCache.set_Stencil(FALSE);
            RCache.set_ColorWriteEnable();
            dsgraph.render_distort();
        }
    }

    RCache.set_Stencil(FALSE);

    // PP enabled ?
    //	Render to RT texture to be able to copy RT even in windowed mode.
    BOOL PP_Complex = u_need_PP();
    if (_menu_pp)
        PP_Complex = FALSE;

    // HOLGER - HACK
    PP_Complex = TRUE;

    // Combine everything + perform AA
    if (RImplementation.o.msaa)
    {
        if (PP_Complex)
            u_setrt(RCache, rt_Generic, nullptr, nullptr, rt_Base_Depth); // LDR RT
        else
            u_setrt(RCache, Device.dwWidth, Device.dwHeight, get_base_rt(), 0, 0, get_base_zb());
    }
    else
    {
        if (PP_Complex)
            u_setrt(RCache, rt_Color, nullptr, nullptr, rt_Base_Depth); // LDR RT
        else
            u_setrt(RCache, Device.dwWidth, Device.dwHeight, get_base_rt(), 0, 0, get_base_zb());
    }
    //. u_setrt				( Device.dwWidth,Device.dwHeight, get_base_rt(), NULL, NULL, get_base_zb());
    RCache.set_CullMode(CULL_NONE);
    RCache.set_Stencil(FALSE);

    if (1)
    {
        PIX_EVENT(combine_2);

        struct v_aa
        {
            Fvector4 p;
            Fvector2 uv0;
            Fvector2 uv1;
            Fvector2 uv2;
            Fvector2 uv3;
            Fvector2 uv4;
            Fvector4 uv5;
            Fvector4 uv6;
        };

        float _w = float(Device.dwWidth);
        float _h = float(Device.dwHeight);
        float ddw = 1.f / _w;
        float ddh = 1.f / _h;
        p0.set(.5f / _w, .5f / _h);
        p1.set((_w + .5f) / _w, (_h + .5f) / _h);

        // Fill vertex buffer
        v_aa* pv = (v_aa*)RImplementation.Vertex.Lock(4, g_aa_AA->vb_stride, Offset);
        pv->p.set(EPS, EPS, EPS, 1.f);
        pv->uv0.set(p0.x, p0.y);
        pv->uv1.set(p0.x - ddw, p0.y - ddh);
        pv->uv2.set(p0.x + ddw, p0.y + ddh);
        pv->uv3.set(p0.x + ddw, p0.y - ddh);
        pv->uv4.set(p0.x - ddw, p0.y + ddh);
        pv->uv5.set(p0.x - ddw, p0.y, p0.y, p0.x + ddw);
        pv->uv6.set(p0.x, p0.y - ddh, p0.y + ddh, p0.x);
        pv++;
        pv->p.set(EPS, float(_h + EPS), EPS, 1.f);
        pv->uv0.set(p0.x, p1.y);
        pv->uv1.set(p0.x - ddw, p1.y - ddh);
        pv->uv2.set(p0.x + ddw, p1.y + ddh);
        pv->uv3.set(p0.x + ddw, p1.y - ddh);
        pv->uv4.set(p0.x - ddw, p1.y + ddh);
        pv->uv5.set(p0.x - ddw, p1.y, p1.y, p0.x + ddw);
        pv->uv6.set(p0.x, p1.y - ddh, p1.y + ddh, p0.x);
        pv++;
        pv->p.set(float(_w + EPS), EPS, EPS, 1.f);
        pv->uv0.set(p1.x, p0.y);
        pv->uv1.set(p1.x - ddw, p0.y - ddh);
        pv->uv2.set(p1.x + ddw, p0.y + ddh);
        pv->uv3.set(p1.x + ddw, p0.y - ddh);
        pv->uv4.set(p1.x - ddw, p0.y + ddh);
        pv->uv5.set(p1.x - ddw, p0.y, p0.y, p1.x + ddw);
        pv->uv6.set(p1.x, p0.y - ddh, p0.y + ddh, p1.x);
        pv++;
        pv->p.set(float(_w + EPS), float(_h + EPS), EPS, 1.f);
        pv->uv0.set(p1.x, p1.y);
        pv->uv1.set(p1.x - ddw, p1.y - ddh);
        pv->uv2.set(p1.x + ddw, p1.y + ddh);
        pv->uv3.set(p1.x + ddw, p1.y - ddh);
        pv->uv4.set(p1.x - ddw, p1.y + ddh);
        pv->uv5.set(p1.x - ddw, p1.y, p1.y, p1.x + ddw);
        pv->uv6.set(p1.x, p1.y - ddh, p1.y + ddh, p1.x);
        pv++;
        RImplementation.Vertex.Unlock(4, g_aa_AA->vb_stride);

        //	Set up variable
        Fvector2 vDofKernel;
        vDofKernel.set(0.5f / Device.dwWidth, 0.5f / Device.dwHeight);
        vDofKernel.mul(ps_r2_dof_kernel_size);

        // Draw COLOR
        if (!RImplementation.o.msaa)
        {
            if (ps_r2_ls_flags.test(R2FLAG_AA))
                RCache.set_Element(s_combine->E[bDistort ? 3 : 1]); // look at blender_combine.cpp
            else
                RCache.set_Element(s_combine->E[bDistort ? 4 : 2]); // look at blender_combine.cpp
        }
        else
        {
            if (ps_r2_ls_flags.test(R2FLAG_AA))
                RCache.set_Element(s_combine_msaa[0]->E[bDistort ? 3 : 1]); // look at blender_combine.cpp
            else
                RCache.set_Element(s_combine_msaa[0]->E[bDistort ? 4 : 2]); // look at blender_combine.cpp
        }
        RCache.set_c("e_barrier", ps_r2_aa_barier.x, ps_r2_aa_barier.y, ps_r2_aa_barier.z, 0.f);
        RCache.set_c("e_weights", ps_r2_aa_weight.x, ps_r2_aa_weight.y, ps_r2_aa_weight.z, 0.f);
        RCache.set_c("e_kernel", ps_r2_aa_kernel, ps_r2_aa_kernel, ps_r2_aa_kernel, 0.f);
        RCache.set_c("m_current", m_current);
        RCache.set_c("m_previous", m_previous);
        RCache.set_c("m_blur", m_blur_scale.x, m_blur_scale.y, 0.f, 0.f);
        Fvector3 dof;
        g_pGamePersistent->GetCurrentDof(dof);
        RCache.set_c("dof_params", dof.x, dof.y, dof.z, ps_r2_dof_sky);
        //.		RCache.set_c				("dof_params",	ps_r2_dof.x, ps_r2_dof.y, ps_r2_dof.z, ps_r2_dof_sky);
        RCache.set_c("dof_kernel", vDofKernel.x, vDofKernel.y, ps_r2_dof_kernel_size, 0.f);

        RCache.set_Geometry(g_aa_AA);
        RCache.Render(D3DPT_TRIANGLELIST, Offset, 0, 4, 0, 2);
    }
    RCache.set_Stencil(FALSE);

    //	if FP16-BLEND !not! supported - draw flares here, overwise they are already in the bloom target
    /* if (!RImplementation.o.fp16_blend)*/
    PIX_EVENT(LENS_FLARES);
    g_pGamePersistent->Environment().RenderFlares(); // lens-flares

    //	PP-if required
    if (PP_Complex)
    {
        PIX_EVENT(phase_pp);
        phase_pp();
    }

    //	Re-adapt luminance
    RCache.set_Stencil(FALSE);

    //*** exposure-pipeline-clear
    {
        std::swap(rt_LUM_pool[gpu_id * 2 + 0], rt_LUM_pool[gpu_id * 2 + 1]);
        t_LUM_src->surface_set(GL_TEXTURE_2D, 0);
        t_LUM_dest->surface_set(GL_TEXTURE_2D, 0);
    }

#ifdef DEBUG
    RCache.set_CullMode(CULL_CCW);
    static xr_vector<Fplane> saved_dbg_planes;
    if (bDebug)
        saved_dbg_planes = dbg_planes;
    else
        dbg_planes = saved_dbg_planes;
    if (1) for (u32 it=0; it<dbg_planes.size(); it++)
	{
		Fplane&		P	=	dbg_planes[it];
		Fvector		zero	;
		zero.mul	(P.n,P.d);

		Fvector             L_dir,L_up=P.n,L_right;
		L_dir.set           (0,0,1);                if (_abs(L_up.dotproduct(L_dir))>.99f)  L_dir.set(1,0,0);
		L_right.crossproduct(L_up,L_dir);           L_right.normalize       ();
		L_dir.crossproduct  (L_right,L_up);         L_dir.normalize         ();

		Fvector				p0,p1,p2,p3;
		float				sz	= 100.f;
		p0.mad				(zero,L_right,sz).mad	(L_dir,sz);
		p1.mad				(zero,L_right,sz).mad	(L_dir,-sz);
		p2.mad				(zero,L_right,-sz).mad	(L_dir,-sz);
		p3.mad				(zero,L_right,-sz).mad	(L_dir,+sz);
		RCache.dbg_DrawTRI	(Fidentity,p0,p1,p2,0xffffffff);
		RCache.dbg_DrawTRI	(Fidentity,p2,p3,p0,0xffffffff);
	}

	static	xr_vector<dbg_line_t>	saved_dbg_lines;
	if (bDebug)		saved_dbg_lines	= dbg_lines;
	else			dbg_lines		= saved_dbg_lines;
	if (1) for (u32 it=0; it<dbg_lines.size(); it++)
	{
		RCache.dbg_DrawLINE		(Fidentity,dbg_lines[it].P0,dbg_lines[it].P1,dbg_lines[it].color);
	}
#endif

    // ********************* Debug
    /*
    if (0)		{
        u32		C					= color_rgba	(255,255,255,255);
        float	_w					= float(Device.dwWidth)/3;
        float	_h					= float(Device.dwHeight)/3;

        // draw light-spheres
#ifdef DEBUG
        if (0) for (u32 it=0; it<dbg_spheres.size(); it++)
        {
            Fsphere				S	= dbg_spheres[it].first;
            Fmatrix				M;
            u32				ccc		= dbg_spheres[it].second.get();
            M.scale					(S.R,S.R,S.R);
            M.translate_over		(S.P);
            RCache.dbg_DrawEllipse	(M,ccc);
            RCache.dbg_DrawAABB		(S.P,.05f,.05f,.05f,ccc);
        }
#endif
        // Draw quater-screen quad textured with our direct-shadow-map-image
        if (1)
        {
            u32							IX=0,IY=1;
            p0.set						(.5f/_w, .5f/_h);
            p1.set						((_w+.5f)/_w, (_h+.5f)/_h );

            // Fill vertex buffer
            FVF::TL* pv					= (FVF::TL*) RImplementation.Vertex.Lock	(4,g_combine->vb_stride,Offset);
            pv->set						((IX+0)*_w+EPS,	(IY+1)*_h+EPS,	EPS,	1.f, C, p0.x, p0.y);	pv++;
            pv->set						((IX+0)*_w+EPS,	(IY+0)*_h+EPS,	EPS,	1.f, C, p0.x, p1.y);	pv++;
            pv->set						((IX+1)*_w+EPS,	(IY+1)*_h+EPS,	EPS,	1.f, C, p1.x, p0.y);	pv++;
            pv->set						((IX+1)*_w+EPS,	(IY+0)*_h+EPS,	EPS,	1.f, C, p1.x, p1.y);	pv++;
            RImplementation.Vertex.Unlock		(4,g_combine->vb_stride);

            // Draw COLOR
            RCache.set_Shader			(s_combine_dbg_0);
            RCache.set_Geometry			(g_combine);
            RCache.Render				(D3DPT_TRIANGLELIST,Offset,0,4,0,2);
        }

        // Draw quater-screen quad textured with our accumulator
        if (0)
        {
            u32							IX=1,IY=1;
            p0.set						(.5f/_w, .5f/_h);
            p1.set						((_w+.5f)/_w, (_h+.5f)/_h );

            // Fill vertex buffer
            FVF::TL* pv					= (FVF::TL*) RImplementation.Vertex.Lock	(4,g_combine->vb_stride,Offset);
            pv->set						((IX+0)*_w+EPS,	(IY+1)*_h+EPS,	EPS,	1.f, C, p0.x, p0.y);	pv++;
            pv->set						((IX+0)*_w+EPS,	(IY+0)*_h+EPS,	EPS,	1.f, C, p0.x, p1.y);	pv++;
            pv->set						((IX+1)*_w+EPS,	(IY+1)*_h+EPS,	EPS,	1.f, C, p1.x, p0.y);	pv++;
            pv->set						((IX+1)*_w+EPS,	(IY+0)*_h+EPS,	EPS,	1.f, C, p1.x, p1.y);	pv++;
            RImplementation.Vertex.Unlock		(4,g_combine->vb_stride);

            // Draw COLOR
            RCache.set_Shader			(s_combine_dbg_1);
            RCache.set_Geometry			(g_combine);
            RCache.Render				(D3DPT_TRIANGLELIST,Offset,0,4,0,2);
        }
    }
    */
#ifdef DEBUG
    dbg_spheres.clear();
    dbg_lines.clear();
    dbg_planes.clear();
#endif
}

void CRenderTarget::phase_wallmarks()
{
    // Targets
    RCache.set_RT(0, 2);
    RCache.set_RT(0, 1);
    u_setrt(RCache, rt_Color, nullptr, nullptr, rt_MSAADepth);
    // Stencil	- draw only where stencil >= 0x1
    RCache.set_Stencil(TRUE, D3DCMP_LESSEQUAL, 0x01, 0xff, 0x00);
    RCache.set_CullMode(CULL_CCW);
    RCache.set_ColorWriteEnable(D3DCOLORWRITEENABLE_RED | D3DCOLORWRITEENABLE_GREEN | D3DCOLORWRITEENABLE_BLUE);
}

void CRenderTarget::phase_combine_volumetric()
{
    PIX_EVENT(phase_combine_volumetric);
    u32 Offset = 0;

    //	TODO: DX11: Remove half pixel offset here
    u_setrt(RCache, rt_Generic_0_r, rt_Generic_1_r, nullptr, rt_MSAADepth);

    //	Sets limits to both render targets
    RCache.set_ColorWriteEnable(D3DCOLORWRITEENABLE_RED | D3DCOLORWRITEENABLE_GREEN | D3DCOLORWRITEENABLE_BLUE);
    {
        // Fill VB
        float scale_X = float(Device.dwWidth) / float(TEX_jitter);
        float scale_Y = float(Device.dwHeight) / float(TEX_jitter);

        // Fill vertex buffer
        FVF::TL* pv = (FVF::TL*)RImplementation.Vertex.Lock(4, g_combine->vb_stride, Offset);
        pv->set(-1, 1, 0, 1, 0, 0, scale_Y);
        pv++;
        pv->set(-1, -1, 0, 0, 0, 0, 0);
        pv++;
        pv->set(1, 1, 1, 1, 0, scale_X, scale_Y);
        pv++;
        pv->set(1, -1, 1, 0, 0, scale_X, 0);
        pv++;
        RImplementation.Vertex.Unlock(4, g_combine->vb_stride);

        // Draw
        RCache.set_Element(s_combine_volumetric->E[0]);
        RCache.set_Geometry(g_combine);
        RCache.Render(D3DPT_TRIANGLELIST, Offset, 0, 4, 0, 2);
    }
    RCache.set_ColorWriteEnable();
}
} // namespace xray::render::RENDER_NAMESPACE
