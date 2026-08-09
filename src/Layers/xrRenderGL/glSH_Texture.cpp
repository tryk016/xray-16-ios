#include "stdafx.h"
#pragma hdrstop

#include "../xrRender/ResourceManager.h"

#ifdef XR_PLATFORM_WINDOWS // TODO
#include "xrEngine/tntQAVI.h"
#endif
#include "xrEngine/xrTheora_Surface.h"

#define PRIORITY_HIGH   12
#define PRIORITY_NORMAL 8
#define PRIORITY_LOW    4

namespace xray::render::RENDER_NAMESPACE
{
void resptrcode_texture::create(LPCSTR _name)
{
    _set(RImplementation.Resources->_CreateTexture(_name));
}

//////////////////////////////////////////////////////////////////////
// Construction/Destruction
//////////////////////////////////////////////////////////////////////
CTexture::CTexture()
{
    pSurface = 0;
    pBuffer = 0;
    pAVI = nullptr;
    pTheora = nullptr;
    desc = GL_TEXTURE_2D;
    desc_cache = 0;
    seqMSPF = 0;
    flags.MemoryUsage = 0;
    flags.bLoaded = false;
    flags.bUser = false;
    flags.seqCycles = FALSE;
    m_material = 1.0f;
    bind = fastdelegate::FastDelegate2<CBackend&,u32>(this, &CTexture::apply_load);
}

CTexture::~CTexture()
{
    Unload();
    // release external reference
    RImplementation.Resources->_DeleteTexture(this);
}

void CTexture::surface_set(GLenum target, GLuint surf)
{
    desc = target;
    pSurface = surf;
    // This wrapper aliases storage owned elsewhere and cannot recreate it by
    // filename after an eviction.
    m_low_memory_pinned = true;
}

GLuint CTexture::surface_get()
{
    // Callers retain the raw GLuint in another CTexture. Ensure the source is
    // live before export and keep it out of lazy file-texture eviction.
    m_last_used_frame = Device.dwFrame;
    if (!flags.bLoaded)
        Load();
    m_low_memory_pinned = true;
    return pSurface;
}

void CTexture::PostLoad()
{
    if (pTheora) bind = fastdelegate::FastDelegate2<CBackend&,u32>(this, &CTexture::apply_theora);
    else if (pAVI) bind = fastdelegate::FastDelegate2<CBackend&,u32>(this, &CTexture::apply_avi);
    else if (!seqDATA.empty()) bind = fastdelegate::FastDelegate2<CBackend&,u32>(this, &CTexture::apply_seq);
    else bind = fastdelegate::FastDelegate2<CBackend&,u32>(this, &CTexture::apply_normal);
}

void CTexture::apply_load(CBackend& cmd_list, u32 dwStage)
{
    m_last_used_frame = Device.dwFrame;
    CHK_GL(glActiveTexture(GL_TEXTURE0 + dwStage));
    if (!flags.bLoaded) Load();
    else PostLoad();
    bind(cmd_list, dwStage);
};

void CTexture::apply_theora(CBackend& cmd_list, u32 dwStage)
{
    // A failed video-surface init used to leave this bind selected with dead members —
    // and on ES the old glMapBuffer call below was a NULL glad pointer, which is exactly
    // how the main menu (animated .ogm background) crashed to PC=0 on device.
    if (!pTheora || !pSurface)
        return;

    m_last_used_frame = Device.dwFrame;
    CHK_GL(glActiveTexture(GL_TEXTURE0 + dwStage));
    CHK_GL(glBindTexture(desc, pSurface));

    if (pTheora->Update(m_play_time != 0xFFFFFFFF ? m_play_time : Device.dwTimeContinual))
    {
        u32* pBits;
        u32 _w = pTheora->Width(true);
        u32 _h = pTheora->Height(true);

        // Clear and map buffer for writing. glMapBufferRange is core in desktop GL 3.0+
        // AND OpenGL ES 3.0 — plain glMapBuffer does not exist on ES (NULL loader entry).
        glBindBuffer(GL_PIXEL_UNPACK_BUFFER, pBuffer);
        CHK_GL(glBufferData(GL_PIXEL_UNPACK_BUFFER, _w * _h * 4, nullptr, GL_STREAM_DRAW)); // Invalidate buffer
        CHK_GL(pBits = (u32*)glMapBufferRange(GL_PIXEL_UNPACK_BUFFER, 0, _w * _h * 4,
            GL_MAP_WRITE_BIT | GL_MAP_INVALIDATE_BUFFER_BIT));
        if (!pBits)
        {
            glBindBuffer(GL_PIXEL_UNPACK_BUFFER, 0);
            return;
        }

        // Write to the buffer and copy it to the texture
        int _pos = 0;
        pTheora->DecompressFrame(pBits, 0, _pos);
        CHK_GL(glUnmapBuffer(GL_PIXEL_UNPACK_BUFFER));
#if defined(XR_PLATFORM_APPLE_IOS)
        // One-shot per texture: proves the Theora decoder actually produced a frame on device.
        // Until slice 6.11 that was inference only — the surface never survived creation, so no
        // decode had ever been observed. Deliberately no pixel readback: the PBO is mapped
        // GL_MAP_WRITE_BIT only, so reading it back is undefined, not merely slow.
        if (!m_video_first_frame_logged)
        {
            m_video_first_frame_logged = true;
            Msg("* iOS video: '%s' first frame decoded %ux%u (surface %dx%d, _pos=%d)",
                cName.c_str(), _w, _h, m_width, m_height, _pos);
        }
#endif
#if defined(XR_PLATFORM_APPLE_IOS)
        // ES 3.0 has no GL_BGRA external format. The decoded frame is BGRA-ordered, so
        // upload as RGBA and let a texture swizzle put the channels right (persists on
        // the texture object; setting it per-frame after bind is redundant but cheap).
        // NB the vector GL_TEXTURE_SWIZZLE_RGBA pname is desktop-only (GL_INVALID_ENUM
        // on ES) — set the four channels individually.
        glTexParameteri(desc, GL_TEXTURE_SWIZZLE_R, GL_BLUE);
        glTexParameteri(desc, GL_TEXTURE_SWIZZLE_G, GL_GREEN);
        glTexParameteri(desc, GL_TEXTURE_SWIZZLE_B, GL_RED);
        glTexParameteri(desc, GL_TEXTURE_SWIZZLE_A, GL_ALPHA);
        CHK_GL(glTexSubImage2D(desc, 0, 0, 0, _w, _h, GL_RGBA, GL_UNSIGNED_BYTE, nullptr));
#else
        CHK_GL(glTexSubImage2D(desc, 0, 0, 0, _w, _h, GL_BGRA, GL_UNSIGNED_BYTE, nullptr));
#endif

        // Unmap the buffer to restore normal texture functionality
        glBindBuffer(GL_PIXEL_UNPACK_BUFFER, 0);
    }
};

void CTexture::apply_avi(CBackend& cmd_list, u32 dwStage) const
{
    m_last_used_frame = Device.dwFrame;
    CHK_GL(glActiveTexture(GL_TEXTURE0 + dwStage));
    CHK_GL(glBindTexture(desc, pSurface));

#ifdef XR_PLATFORM_WINDOWS // TODO
    if (pAVI->NeedUpdate())
    {
        // AVI
        u8* ptr{};
        pAVI->GetFrame(&ptr);
        CHK_GL(glTexSubImage2D(desc, 0, 0, 0, m_width, m_height,
            GL_RGBA, GL_UNSIGNED_BYTE, ptr));
    }
#endif
};

void CTexture::apply_seq(CBackend& cmd_list, u32 dwStage)
{
    m_last_used_frame = Device.dwFrame;
    // SEQ
    u32 frame = Device.dwTimeContinual / seqMSPF; //Device.dwTimeGlobal
    u32 frame_data = seqDATA.size();
    if (flags.seqCycles)
    {
        u32 frame_id = frame % (frame_data * 2);
        if (frame_id >= frame_data) frame_id = frame_data - 1 - frame_id % frame_data;
        pSurface = seqDATA[frame_id];
    }
    else
    {
        u32 frame_id = frame % frame_data;
        pSurface = seqDATA[frame_id];
    }

    CHK_GL(glActiveTexture(GL_TEXTURE0 + dwStage));
    CHK_GL(glBindTexture(desc, pSurface));
};

void CTexture::apply_normal(CBackend& cmd_list, u32 dwStage) const
{
    m_last_used_frame = Device.dwFrame;
    CHK_GL(glActiveTexture(GL_TEXTURE0 + dwStage));
    CHK_GL(glBindTexture(desc, pSurface));
};

void CTexture::Preload()
{
    m_bumpmap = RImplementation.Resources->m_textures_description.GetBumpName(cName);
    m_material = RImplementation.Resources->m_textures_description.GetMaterial(cName);
}

// Fills the CURRENTLY BOUND RGBA8 texture with the YUV triple that yuv2rgb.ps maps to black
// (Y=16, U=V=128). Bytes are written in the DECODER's memory order [V,U,Y,255] — see the packer
// at xrEngine/xrTheora_Surface.cpp:264 (`255<<24 | u<<8 | v` with `y<<16`) — so this fill and a
// real decoded frame travel the identical format/swizzle path.
// Why this is needed at all: a zero sample is NOT black through yuv2rgb.ps. Its constant bias
// _S = (-0.86961, +0.53076, -1.0786) clamps a (0,0,0) sample to RGB(0, 0.531, 0) — precisely the
// flat green quad seen on device. Anything sampled by the movie shader must be YUV-neutral,
// never zero.
static void video_fill_neutral(GLenum target, GLsizei w, GLsizei h)
{
    if (w <= 0 || h <= 0)
        return;
    // A=255, Y=16, U=128, V=128 -> little-endian bytes [0x80 V, 0x80 U, 0x10 Y, 0xFF A]
    xr_vector<u32> px(size_t(w) * size_t(h), 0xFF108080u);
    // A bound PIXEL_UNPACK_BUFFER would reinterpret px.data() as a buffer offset — make sure
    // client-memory upload is what actually happens.
    glBindBuffer(GL_PIXEL_UNPACK_BUFFER, 0);
#if defined(XR_PLATFORM_APPLE_IOS)
    // ES 3.0 has no GL_BGRA external format — upload as RGBA and let the per-channel swizzle put
    // the channels right, exactly as apply_theora does above. The swizzle lives on the texture
    // OBJECT, so it also covers a later apply_normal bind of this same surface (the fallback
    // case), where nothing would re-apply it.
    glTexParameteri(target, GL_TEXTURE_SWIZZLE_R, GL_BLUE);
    glTexParameteri(target, GL_TEXTURE_SWIZZLE_G, GL_GREEN);
    glTexParameteri(target, GL_TEXTURE_SWIZZLE_B, GL_RED);
    glTexParameteri(target, GL_TEXTURE_SWIZZLE_A, GL_ALPHA);
    glTexSubImage2D(target, 0, 0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE, px.data());
#else
    glTexSubImage2D(target, 0, 0, 0, w, h, GL_BGRA, GL_UNSIGNED_BYTE, px.data());
#endif
}

void CTexture::Load()
{
    flags.bLoaded = true;
    desc_cache = 0;
    if (pSurface)
        return;

    flags.bUser = false;
    flags.MemoryUsage = 0;
    m_low_memory_pinned = false;
    if (nullptr == cName.c_str())
        return;
    if (0 == xr_stricmp(cName.c_str(), "$null")) return;
    // we need to check only the beginning of the string,
    // so let's use strncmp instead of strstr.
    if (0 == strncmp(cName.c_str(), "$user$", sizeof("$user$") - 1))
    {
        flags.bUser = true;
        m_low_memory_pinned = true;
        return;
    }

    ZoneScoped;

    Preload();

    // Check for OGM
    string_path fn;
    if (FS.exist(fn, "$game_textures$", cName.c_str(), ".ogm"))
    {
        m_low_memory_pinned = true;
        // AVI
        pTheora = xr_new<CTheoraSurface>();
        m_play_time = 0xFFFFFFFF;

        if (!pTheora->Load(fn))
        {
            xr_delete(pTheora);
            FATAL("Can't open video stream");
        }
        else
        {
            flags.MemoryUsage = pTheora->Width(true) * pTheora->Height(true) * 4;
            pTheora->Play(TRUE, Device.dwTimeContinual);

            // Now create texture
            GLuint pTexture = 0;
            u32 _w = pTheora->Width(false);
            u32 _h = pTheora->Height(false);

            // GL error flags are STICKY and CHK_GL(expr) is just expr in release builds
            // (xrDebug_macros.h:203), so the single trailing glGetError() this code used to do
            // reported whatever ANY earlier path in the frame had left pending and then deleted a
            // perfectly good video texture. That is the whole main-menu green-quad bug: the
            // element stayed on the yuv2rgb shader with pSurface==0, apply_normal bound texture 0,
            // and on ES an unbound sampler returns (0,0,0,1) with no error at all.
            // Drain first, then check PER STAGE — same idiom as the DXT decoder
            // (glTexture.cpp:238-253) — so the next device log names the stage that failed.
            while (glGetError() != GL_NO_ERROR)
                ;

            GLenum err = GL_NO_ERROR;

            glGenBuffers(1, &pBuffer);
            glBindBuffer(GL_PIXEL_UNPACK_BUFFER, pBuffer);
            glBufferData(GL_PIXEL_UNPACK_BUFFER, flags.MemoryUsage, nullptr, GL_STREAM_DRAW);
            glBindBuffer(GL_PIXEL_UNPACK_BUFFER, 0);
            err = glGetError();
            if (err != GL_NO_ERROR)
                Msg("! OpenGL: 0x%x: video PBO alloc (%u bytes) failed: '%s'",
                    err, u32(flags.MemoryUsage), cName.c_str());

            if (err == GL_NO_ERROR)
            {
                glGenTextures(1, &pTexture);
                glBindTexture(GL_TEXTURE_2D, pTexture);
                glTexStorage2D(GL_TEXTURE_2D, 1, GL_RGBA8, _w, _h);
                err = glGetError();
                if (err != GL_NO_ERROR)
                    Msg("! OpenGL: 0x%x: video storage RGBA8 %ux%u failed: '%s'",
                        err, _w, _h, cName.c_str());
            }

            if (err == GL_NO_ERROR)
            {
                // Storage is allocated at the pow2-ceil size Width/Height(false) while frames are
                // uploaded at the real size Width/Height(true) (apply_theora above). For a non-pow2
                // .ogm the margin would keep uninitialised immutable-storage content and render as
                // a bias-green border. Clear the whole surface to YUV black once; this also stamps
                // the iOS channel swizzle onto the texture object.
                video_fill_neutral(GL_TEXTURE_2D, GLsizei(_w), GLsizei(_h));
                err = glGetError();
                if (err != GL_NO_ERROR)
                    Msg("! OpenGL: 0x%x: video initial clear %ux%u failed: '%s'",
                        err, _w, _h, cName.c_str());
            }

            if (err != GL_NO_ERROR)
            {
                // NON-DESTRUCTIVE FAILURE. dxUIRender::UpdateShaderName (dxUIRender.cpp:152-162)
                // has ALREADY switched this element to "hud\movie" (yuv2rgb) purely because the
                // .ogm file exists — that decision is made before and independently of this load
                // and cannot be undone from here. So the invariant we must preserve is: the movie
                // shader always samples a COMPLETE texture whose content decodes to black. Never
                // leave pSurface==0; that is exactly what paints the green quad.
                Msg("! Video stream '%s' disabled after GL error; substituting a black surface.",
                    cName.c_str());
                xr_delete(pTheora);
                if (pTexture)
                {
                    glDeleteTextures(1, &pTexture);
                    pTexture = 0;
                }
                if (pBuffer)
                {
                    glDeleteBuffers(1, &pBuffer);
                    pBuffer = 0;
                }
                while (glGetError() != GL_NO_ERROR)
                    ;

                // 1x1 immutable RGBA8 holding YUV black. Immutable-format textures are
                // mipmap-complete for their level count, and the engine drives filtering through
                // sampler objects (glState.cpp), so a single-level surface samples cleanly.
                glGenTextures(1, &pTexture);
                glBindTexture(GL_TEXTURE_2D, pTexture);
                glTexStorage2D(GL_TEXTURE_2D, 1, GL_RGBA8, 1, 1);
                video_fill_neutral(GL_TEXTURE_2D, 1, 1);
                if (glGetError() != GL_NO_ERROR)
                {
                    // GL is broken beyond anything we can compensate for here.
                    glDeleteTextures(1, &pTexture);
                    pTexture = 0;
                }
                flags.MemoryUsage = 4;
                // The GL surface is 1x1, but report the real storage dimensions so nothing
                // downstream sizes a UI element to one texel. (These members were previously left
                // UNINITIALISED on this path — desc_update()'s glGetTexLevelParameteriv is NULL on
                // ES — so this is an improvement either way.)
                m_width = GLint(_w ? _w : 1);
                m_height = GLint(_h ? _h : 1);
            }
            else
            {
                // glGetTexLevelParameteriv does not exist on ES 3.0, so desc_update() cannot
                // recover these later (see desc_update below) — record them now.
                m_width = GLint(_w);
                m_height = GLint(_h);
            }

            pSurface = pTexture;
            desc = GL_TEXTURE_2D;
        }
    }
    else if (FS.exist(fn, "$game_textures$", cName.c_str(), ".avi"))
    {
        m_low_memory_pinned = true;
#ifdef XR_PLATFORM_WINDOWS // TODO
        // AVI
        pAVI = xr_new<CAviPlayerCustom>();

        if (!pAVI->Load(fn))
        {
            xr_delete(pAVI);
            FATAL("Can't open video stream");
        }
        else
        {
            flags.MemoryUsage = pAVI->m_dwWidth * pAVI->m_dwHeight * 4;

            // Create pixel buffer object
            glGenBuffers(1, &pBuffer);
            glBindBuffer(GL_PIXEL_UNPACK_BUFFER, pBuffer);
            CHK_GL(glBufferData(GL_PIXEL_UNPACK_BUFFER, flags.MemoryUsage, nullptr, GL_STREAM_DRAW));

            // Now create texture to copy PBO into
            GLuint pTexture = 0;
            glGenTextures(1, &pTexture);
            glBindTexture(GL_TEXTURE_2D, pTexture);
            CHK_GL(glTexStorage2D(GL_TEXTURE_2D, 1, GL_RGBA8, pAVI->m_dwWidth, pAVI->m_dwHeight));

            pSurface = pTexture;
            desc = GL_TEXTURE_2D;
            if (glGetError() != GL_NO_ERROR)
            {
                FATAL("Invalid video stream");
                xr_delete(pAVI);
                pSurface = 0;
            }
        }
#endif
    }
    else if (FS.exist(fn, "$game_textures$", cName.c_str(), ".seq"))
    {
        m_low_memory_pinned = true;
        // Sequence
        string256 buffer;
        IReader* _fs = FS.r_open(fn);

        flags.seqCycles = FALSE;
        _fs->r_string(buffer, sizeof buffer);
        if (0 == xr_stricmp(buffer, "cycled"))
        {
            flags.seqCycles = TRUE;
            _fs->r_string(buffer, sizeof buffer);
        }
        u32 fps = atoi(buffer);
        seqMSPF = 1000 / fps;

        while (!_fs->eof())
        {
            _fs->r_string(buffer, sizeof buffer);
            _Trim(buffer);
            if (buffer[0])
            {
                // Load another texture
                u32 mem = 0;
                pSurface = RImplementation.texture_load(buffer, mem, desc, m_width, m_height);
                if (pSurface)
                {
                    // pSurface->SetPriority	(PRIORITY_LOW);
                    seqDATA.push_back(pSurface);
                    flags.MemoryUsage += mem;
                }
            }
        }
        pSurface = 0;
        FS.r_close(_fs);
    }
    else
    {
        // Normal texture
        u32 mem = 0;
        pSurface = RImplementation.texture_load(cName.c_str(), mem, desc, m_width, m_height);

        // Calc memory usage and preload into vid-mem
        if (pSurface)
        {
            // pSurface->SetPriority	(PRIORITY_NORMAL);
            flags.MemoryUsage = mem;
        }
    }

    PostLoad();
}

void CTexture::Unload()
{
    ZoneScoped;
#ifdef DEBUG
    string_path				msg_buff;
    sprintf_s(msg_buff, sizeof(msg_buff), "* Unloading texture [%s] pSurface ID=%d", cName.c_str(), pSurface);
#endif // DEBUG

    //.	if (flags.bLoaded)		Msg		("* Unloaded: %s",cName.c_str());

    flags.bLoaded = FALSE;
    if (!seqDATA.empty())
    {
        CHK_GL(glDeleteTextures(seqDATA.size(), seqDATA.data()));
        seqDATA.clear();
        pSurface = 0;
    }

    CHK_GL(glDeleteTextures(1, &pSurface));
    pSurface = 0;
    CHK_GL(glDeleteBuffers(1, &pBuffer));
    pBuffer = 0;

#ifdef XR_PLATFORM_WINDOWS
    xr_delete(pAVI);
#endif
    xr_delete(pTheora);

    flags.MemoryUsage = 0;
    m_video_first_frame_logged = false;

    bind = fastdelegate::FastDelegate2<CBackend&,u32>(this, &CTexture::apply_load);
}

void CTexture::desc_update()
{
    desc_cache = pSurface;
    // glGetTexLevelParameteriv doesn't exist in OpenGL ES 3.0 (it's 3.1+) — the glad
    // entry is NULL on device and calling it crashed the font renderer. On ES the
    // dimensions are recorded at load time (texture_load reports the level-0 extent);
    // keep the GL query for desktop paths that bypass texture_load (e.g. RT wraps).
    if (glGetTexLevelParameteriv && pSurface && (GL_TEXTURE_2D == desc || GL_TEXTURE_2D_MULTISAMPLE == desc))
    {
        glBindTexture(desc, pSurface);
        CHK_GL(glGetTexLevelParameteriv(desc, 0, GL_TEXTURE_WIDTH, &m_width));
        CHK_GL(glGetTexLevelParameteriv(desc, 0, GL_TEXTURE_HEIGHT, &m_height));
    }
}

void CTexture::video_Play(BOOL looped, u32 _time)
{
    if (pTheora) pTheora->Play(looped, _time != 0xFFFFFFFF ? (m_play_time = _time) : Device.dwTimeContinual);
}

void CTexture::video_Pause(BOOL state) const
{
    if (pTheora) pTheora->Pause(state);
}

void CTexture::video_Stop() const
{
    if (pTheora) pTheora->Stop();
}

BOOL CTexture::video_IsPlaying() const
{
    return pTheora ? pTheora->IsPlaying() : FALSE;
}
} // namespace xray::render::RENDER_NAMESPACE
