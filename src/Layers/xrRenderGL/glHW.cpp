// glHW.cpp: implementation of the OpenGL specialisation of CHW.
//////////////////////////////////////////////////////////////////////

#include "stdafx.h"
#pragma hdrstop

#include "glHW.h"
#include "xrEngine/XR_IOConsole.h"

#if defined(XR_PLATFORM_APPLE_IOS)
#include <SDL_syswm.h>
#include "xrEngine/ios/ios_display.h"
#endif

namespace xray::render::RENDER_NAMESPACE
{
CHW HW;

void CALLBACK OnDebugCallback(GLenum /*source*/, GLenum /*type*/, GLuint id, GLenum severity, GLsizei /*length*/,
    const GLchar* message, const void* /*userParam*/)
{
    if (severity != GL_DEBUG_SEVERITY_NOTIFICATION)
        Log(message, id);
}

static_assert(std::is_same_v<decltype(&OnDebugCallback), GLDEBUGPROC>);

void UpdateVSync()
{
    if (psDeviceFlags.test(rsVSync))
    {
        // Try adaptive vsync first
        if (SDL_GL_SetSwapInterval(-1) == -1)
            SDL_GL_SetSwapInterval(1);
    }
    else
    {
        SDL_GL_SetSwapInterval(0);
    }
}

CHW::CHW()
{
    if (!ThisInstanceIsGlobal())
        return;

    Device.seqAppActivate.Add(this);
    Device.seqAppDeactivate.Add(this);
}

CHW::~CHW()
{
    if (!ThisInstanceIsGlobal())
        return;

    Device.seqAppActivate.Remove(this);
    Device.seqAppDeactivate.Remove(this);
}

void CHW::OnAppActivate()
{
    if (m_window)
    {
#if defined(XR_PLATFORM_APPLE_IOS)
        Msg("* iOS: app activate");
#else
        SDL_RestoreWindow(m_window);
#endif
    }
}

void CHW::OnAppDeactivate()
{
    if (m_window)
    {
#if defined(XR_PLATFORM_APPLE_IOS)
        // Desktop ALT-TAB behavior: minimize the fullscreen window on focus loss. On iOS
        // minimizing IS backgrounding — any transient resign-active (notification banner,
        // auto-lock during a long touch-less level load, control-center peek) would send
        // the app to background for good; the process then sits suspended (seen in a
        // JetsamEvent snapshot) and the user reads it as a crash. Never minimize here.
        Msg("* iOS: app deactivate");
#else
        if (psDeviceMode.WindowStyle == rsFullscreen || psDeviceMode.WindowStyle == rsFullscreenBorderless)
            SDL_MinimizeWindow(m_window);
#endif
    }
}

//////////////////////////////////////////////////////////////////////
// Construction/Destruction
//////////////////////////////////////////////////////////////////////
void CHW::CreateDevice(SDL_Window* hWnd)
{
    ZoneScoped;

    m_window = hWnd;

    R_ASSERT(m_window);

    // Choose the closest pixel format
    SDL_DisplayMode mode;
    SDL_GetWindowDisplayMode(m_window, &mode);
    mode.format = SDL_PIXELFORMAT_RGBA8888;
    // Apply the pixel format to the device context
    SDL_SetWindowDisplayMode(m_window, &mode);

    Caps.fTarget = D3DFMT_A8R8G8B8;
    Caps.fDepth = D3DFMT_D24S8;

    // Create the context
    m_context = SDL_GL_CreateContext(m_window);
    if (m_context == nullptr)
    {
        Log("! OpenGL: could not create drawing context:", SDL_GetError());
        return;
    }

    if (MakeContextCurrent(IRender::PrimaryContext) != 0)
    {
        Log("! OpenGL: could not make context current:", SDL_GetError());
        return;
    }

#if defined(XR_PLATFORM_APPLE_IOS)
    if (!ios_display::set_opengl_drawable_scale(m_window, ios_display::OpenGLDrawableScale))
        Log("! iOS: could not set the OpenGL drawable scale");
#endif

    int version;
    {
        ZoneScopedN("gladLoadGL");
#if defined(XR_PLATFORM_APPLE_IOS)
        // iOS provides OpenGL ES only; load the GLES2/3 entry points from the
        // merged glad loader instead of the desktop GL table.
        version = gladLoadGLES2(reinterpret_cast<GLADloadfunc>(SDL_GL_GetProcAddress));
#else
        version = gladLoadGL(reinterpret_cast<GLADloadfunc>(SDL_GL_GetProcAddress));
#endif
    }
    if (version == 0)
    {
        Log("! OpenGL: could not initialize GLAD.");
        if (const auto err = SDL_GetError())
            Log("SDL Error:", err);
        return;
    }

    if (ThisInstanceIsGlobal())
    {
        UpdateVSync();

#ifdef DEBUG
        if (glDebugMessageCallback)
        {
            CHK_GL(glEnable(GL_DEBUG_OUTPUT));
            CHK_GL(glDebugMessageCallback((GLDEBUGPROC)OnDebugCallback, nullptr));
        }
#endif // DEBUG
    }

    int iMaxVTFUnits, iMaxCTIUnits;
    glGetIntegerv(GL_MAX_VERTEX_TEXTURE_IMAGE_UNITS, &iMaxVTFUnits);
    glGetIntegerv(GL_MAX_COMBINED_TEXTURE_IMAGE_UNITS, &iMaxCTIUnits);

    AdapterName = reinterpret_cast<pcstr>(glGetString(GL_RENDERER));
    OpenGLVersionString = reinterpret_cast<pcstr>(glGetString(GL_VERSION));
    ShadingVersion = reinterpret_cast<pcstr>(glGetString(GL_SHADING_LANGUAGE_VERSION));

    Msg("* GPU vendor: [%s] device: [%s]", glGetString(GL_VENDOR), AdapterName);
    Msg("* GPU OpenGL version: %s", OpenGLVersionString);
    Msg("* GPU OpenGL shading language version: %s", ShadingVersion);
    Msg("* GPU OpenGL VTF units: [%d] CTI units: [%d]", iMaxVTFUnits, iMaxCTIUnits);

#if defined(XR_PLATFORM_APPLE_IOS)
    {
        // The deferred renderer's G-buffer/accum/HDR targets are float (RGBA16F/R32F/...).
        // On OpenGL ES those are only renderable with EXT_color_buffer_float / _half_float;
        // without them every deferred FBO comes out INCOMPLETE = black world. Log the
        // relevant caps so the first-3D-frame device log is self-diagnosing (release-safe,
        // pure logging). glGetString(GL_EXTENSIONS) is null in core ES 3.0 — enumerate.
        GLint numExt = 0;
        glGetIntegerv(GL_NUM_EXTENSIONS, &numExt);
        bool cbFloat = false, cbHalf = false, texFloatLin = false, texHalfLin = false, borderClamp = false;
        for (GLint i = 0; i < numExt; ++i)
        {
            pcstr e = reinterpret_cast<pcstr>(glGetStringi(GL_EXTENSIONS, i));
            if (!e)
                continue;
            if (0 == xr_strcmp(e, "GL_EXT_color_buffer_float")) cbFloat = true;
            else if (0 == xr_strcmp(e, "GL_EXT_color_buffer_half_float")) cbHalf = true;
            else if (0 == xr_strcmp(e, "GL_OES_texture_float_linear")) texFloatLin = true;
            else if (0 == xr_strcmp(e, "GL_OES_texture_half_float_linear")) texHalfLin = true;
            else if (0 == xr_strcmp(e, "GL_EXT_texture_border_clamp")) borderClamp = true;
        }
        Msg("* GPU ES caps: color_buffer_float[%d] color_buffer_half_float[%d] "
            "tex_float_linear[%d] tex_half_float_linear[%d] border_clamp[%d] (of %d exts)",
            cbFloat, cbHalf, texFloatLin, texHalfLin, borderClamp, numExt);
    }
#endif

    ComputeShadersSupported = false; // XXX: Implement compute shaders support

    if (glGenFramebuffers && glBindFramebuffer)
        UpdateViews();
}

void CHW::DestroyDevice()
{
    CHK_GL(glDeleteFramebuffers(1, &pFB));
    pFB = 0;

    const auto context = SDL_GL_GetCurrentContext();
    if (context == m_context)
        SDL_GL_MakeCurrent(nullptr, nullptr);

    SDL_GL_DeleteContext(m_context);
    m_context = nullptr;
}

//////////////////////////////////////////////////////////////////////
// Resetting device
//////////////////////////////////////////////////////////////////////
void CHW::Reset()
{
    ZoneScoped;

    CHK_GL(glDeleteFramebuffers(1, &pFB));
    pFB = 0;
    UpdateViews();

    UpdateVSync();
}

void CHW::SetPrimaryAttributes(u32& windowFlags)
{
    windowFlags |= SDL_WINDOW_OPENGL;

#if defined(XR_PLATFORM_APPLE_IOS)
    // iOS only exposes OpenGL ES through SDL's UIKit/EAGL backend. Requesting a
    // desktop core profile makes SDL hand back an ES context that mismatches the
    // desktop glad table, so ask for ES 3.0 explicitly here.
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK, SDL_GL_CONTEXT_PROFILE_ES);
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_MAJOR_VERSION, 3);
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_MINOR_VERSION, 0);
#else
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK, SDL_GL_CONTEXT_PROFILE_CORE);
#endif

    SDL_GL_SetAttribute(SDL_GL_RED_SIZE, 8);
    SDL_GL_SetAttribute(SDL_GL_GREEN_SIZE, 8);
    SDL_GL_SetAttribute(SDL_GL_BLUE_SIZE, 8);
    SDL_GL_SetAttribute(SDL_GL_ALPHA_SIZE, 8);

    SDL_GL_SetAttribute(SDL_GL_DOUBLEBUFFER, 1);
    SDL_GL_SetAttribute(SDL_GL_DEPTH_SIZE, 24);
    SDL_GL_SetAttribute(SDL_GL_STENCIL_SIZE, 8);

#if !defined(XR_PLATFORM_APPLE_IOS)
    if (!strstr(Core.Params, "-no_gl_context"))
    {
        SDL_GL_SetAttribute(SDL_GL_CONTEXT_MAJOR_VERSION, 4);
        SDL_GL_SetAttribute(SDL_GL_CONTEXT_MINOR_VERSION, 1);
    }
#endif
}

IRender::RenderContext CHW::GetCurrentContext() const
{
    const auto context = SDL_GL_GetCurrentContext();
    if (context == m_context)
        return IRender::PrimaryContext;
    return IRender::NoContext;
}

int CHW::MakeContextCurrent(IRender::RenderContext context) const
{
    switch (context)
    {
    case IRender::NoContext:
        return SDL_GL_MakeCurrent(nullptr, nullptr);

    case IRender::PrimaryContext:
        return SDL_GL_MakeCurrent(m_window, m_context);

    default:
        NODEFAULT;
    }
    return -1;
}

void CHW::UpdateViews()
{
    // Create the default framebuffer
    glGenFramebuffers(1, &pFB);
    CHK_GL(glBindFramebuffer(GL_FRAMEBUFFER, pFB));

    BackBufferCount = 1;
}

void CHW::BeginScene() { }
void CHW::EndScene() { }

void CHW::Present()
{
#if 0 // kept for historical reasons
    RImplementation.Target->phase_flip();
#else
    // The final image lives in the engine's own FBO (pFB); presenting means blitting it
    // to the window's framebuffer. On desktop that is FBO 0 — but iOS HAS NO default
    // framebuffer: the screen is the FBO SDL's GL view created (SysWM uikit.framebuffer).
    // Blitting to 0 on iOS raised GL_INVALID_FRAMEBUFFER_OPERATION every frame and left
    // the screen black under a perfectly running menu.
    GLuint screenFB = 0;
    GLint dstW = Device.dwWidth, dstH = Device.dwHeight;
#if defined(XR_PLATFORM_APPLE_IOS)
    {
        SDL_SysWMinfo info;
        SDL_VERSION(&info.version);
        if (SDL_GetWindowWMInfo(m_window, &info))
            screenFB = info.info.uikit.framebuffer;
        int dw = 0, dh = 0;
        SDL_GL_GetDrawableSize(m_window, &dw, &dh);
        if (dw && dh)
        {
            dstW = dw;
            dstH = dh;
        }
    }
#endif
    glBindFramebuffer(GL_READ_FRAMEBUFFER, pFB);
    glBindFramebuffer(GL_DRAW_FRAMEBUFFER, screenFB);

#if defined(XR_PLATFORM_APPLE_IOS)
    // iOS diagnostics: periodic full-frame dump, so visual regressions can be inspected over the
    // cable instead of being described by hand. It reads pFB while that target is still bound as
    // GL_READ_FRAMEBUFFER, so what lands on disk is exactly what reaches the screen.
    //
    // Written as binary PPM (P6): self-describing, no image library, and trivial to convert
    // on the host - see misc/ios/shot.sh, which pulls and converts it. GL rows come out
    // bottom-up, so they are emitted in reverse to give a top-down image.
    //
    // Cost is a full glReadPixels plus a file write, both of which stall the pipeline. Normal
    // gameplay and performance runs therefore leave ios_diagnostics at its default of 0.
    if (psIOSDiagnostics)
    {
        static u32 s_ios_shot_next = 0;
        // dwTimeGlobal freezes while the game is paused (including Options).
        // Continual time still advances with rendered menu frames.
        if (Device.dwTimeContinual >= s_ios_shot_next)
        {
            s_ios_shot_next = Device.dwTimeContinual + 5000;

            const GLint w = GLint(Device.dwWidth);
            const GLint h = GLint(Device.dwHeight);
            if (w > 0 && h > 0)
            {
                const size_t rgbaBytes = size_t(w) * size_t(h) * 4;
                u8* rgba = (u8*)xr_malloc(rgbaBytes);
                if (rgba)
                {
                    // reads from GL_READ_FRAMEBUFFER, which is pFB - bound just above
                    glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE, rgba);

                    const char* home = getenv("HOME");
                    string_path tmp, dst, meta_tmp, meta_dst;
                    xr_sprintf(tmp, "%s/Documents/xr_shot.tmp", home ? home : ".");
                    xr_sprintf(dst, "%s/Documents/xr_shot.ppm", home ? home : ".");
                    xr_sprintf(meta_tmp, "%s/Documents/xr_shot_meta.tmp", home ? home : ".");
                    xr_sprintf(meta_dst, "%s/Documents/xr_shot_meta.txt", home ? home : ".");

                    // Write to a temp name and rename, so a pull over the cable can never
                    // catch a half-written file.
                    if (FILE* f = fopen(tmp, "wb"))
                    {
                        fprintf(f, "P6\n%d %d\n255\n", int(w), int(h));
                        for (GLint y = h - 1; y >= 0; --y)
                        {
                            const u8* row = rgba + size_t(y) * size_t(w) * 4;
                            for (GLint x = 0; x < w; ++x)
                                fwrite(row + size_t(x) * 4, 1, 3, f);
                        }
                        fclose(f);
                        const int rc = rename(tmp, dst);

                        // Publish a generation only after the frame is atomically visible.
                        // shot.sh waits for this token to change, so it cannot mistake a
                        // capture left by a previous process for the current frame.
                        if (rc == 0)
                        {
                            if (FILE* meta = fopen(meta_tmp, "wb"))
                            {
                                fprintf(meta, "%u %u\n", Device.dwFrame, Device.dwTimeContinual);
                                fclose(meta);
                                rename(meta_tmp, meta_dst);
                            }
                        }

                        u32 mx = 0;
                        for (size_t i = 0; i < rgbaBytes; ++i)
                            if (rgba[i] > mx)
                                mx = rgba[i];
                        Msg("* iOS diag: shot %dx%d maxByte=%u rename=%d", int(w), int(h), mx, rc);
                    }
                    else
                        Msg("* iOS diag: shot fopen FAILED (%s)", tmp);
                    xr_free(rgba);
                }
            }
        }
    }
#endif

    glBlitFramebuffer(
        0, 0, Device.dwWidth, Device.dwHeight,
        0, 0, dstW, dstH,
        GL_COLOR_BUFFER_BIT, GL_NEAREST);
#endif

    SDL_GL_SwapWindow(m_window);
    CurrentBackBuffer = (CurrentBackBuffer + 1) % BackBufferCount;
}

DeviceState CHW::GetDeviceState() const
{
    //  TODO: OGL: Implement GetDeviceState
    return DeviceState::Normal;
}

std::pair<u32, u32> CHW::GetSurfaceSize()
{
#if defined(XR_PLATFORM_APPLE_IOS)
    int width = 0;
    int height = 0;
    SDL_GL_GetDrawableSize(HW.m_window, &width, &height);
    if (width > 0 && height > 0)
    {
        Msg("* iOS: OpenGL drawable %dx%d (UIKit window %ux%u, scale %.1f)",
            width, height, psDeviceMode.Width, psDeviceMode.Height,
            ios_display::OpenGLDrawableScale);
        return { static_cast<u32>(width), static_cast<u32>(height) };
    }

    Log("! iOS: SDL_GL_GetDrawableSize failed, falling back to UIKit point size");
#else
#endif
    return { psDeviceMode.Width, psDeviceMode.Height };
}

bool CHW::ThisInstanceIsGlobal() const
{
    return this == &HW;
}

void CHW::BeginPixEvent(pcstr name) const
{
    if (glPushDebugGroup)
        glPushDebugGroup(GL_DEBUG_SOURCE_APPLICATION, 0, -1, name);
}

void CHW::EndPixEvent() const
{
    if (glPushDebugGroup)
        glPopDebugGroup();
}
} // namespace xray::render::RENDER_NAMESPACE
