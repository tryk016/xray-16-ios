// Texture.cpp: implementation of the CTexture class.
//
//////////////////////////////////////////////////////////////////////

#include "stdafx.h"

#include <gli/gli.hpp>

namespace xray::render::RENDER_NAMESPACE
{
#if defined(XR_PLATFORM_APPLE_IOS)
// ---- DXT/S3TC software decode -------------------------------------------------------
// Apple's GL-on-Metal (OpenGL ES 3.0) exposes no S3TC/DXT texture formats, and virtually
// every CoP .dds asset is DXT1/3/5 — every upload failed (GL 0x500/0x501) leaving a black
// screen under a perfectly healthy engine. Decode DXT blocks on the CPU to RGBA8 at load
// time. Memory cost is 4-8x per texture; correctness first (the long-term plan is an
// offline ASTC transcode of the gamedata, iOS-Port-Plan 4.9).
namespace dxt
{
struct rgba { u8 r, g, b, a; };

static void decode_color_block(const u8* s, rgba out[16], bool dxt1)
{
    const u16 c0 = u16(s[0] | (s[1] << 8));
    const u16 c1 = u16(s[2] | (s[3] << 8));
    const auto expand = [](u16 c) -> rgba {
        return { u8(((c >> 11) & 31) * 255 / 31), u8(((c >> 5) & 63) * 255 / 63),
                 u8((c & 31) * 255 / 31), 255 };
    };
    rgba pal[4];
    pal[0] = expand(c0);
    pal[1] = expand(c1);
    if (!dxt1 || c0 > c1)
    {
        pal[2] = { u8((2 * pal[0].r + pal[1].r) / 3), u8((2 * pal[0].g + pal[1].g) / 3),
                   u8((2 * pal[0].b + pal[1].b) / 3), 255 };
        pal[3] = { u8((pal[0].r + 2 * pal[1].r) / 3), u8((pal[0].g + 2 * pal[1].g) / 3),
                   u8((pal[0].b + 2 * pal[1].b) / 3), 255 };
    }
    else // 1-bit-alpha DXT1 mode
    {
        pal[2] = { u8((pal[0].r + pal[1].r) / 2), u8((pal[0].g + pal[1].g) / 2),
                   u8((pal[0].b + pal[1].b) / 2), 255 };
        pal[3] = { 0, 0, 0, 0 };
    }
    const u32 bits = u32(s[4]) | (u32(s[5]) << 8) | (u32(s[6]) << 16) | (u32(s[7]) << 24);
    for (int i = 0; i < 16; ++i)
        out[i] = pal[(bits >> (2 * i)) & 3];
}

static void decode_alpha_block(const u8* s, u8 out[16]) // BC3 alpha / BC4-BC5 channel
{
    const u8 a0 = s[0], a1 = s[1];
    u8 pal[8];
    pal[0] = a0;
    pal[1] = a1;
    if (a0 > a1)
        for (int i = 1; i < 7; ++i)
            pal[1 + i] = u8(((7 - i) * a0 + i * a1) / 7);
    else
    {
        for (int i = 1; i < 5; ++i)
            pal[1 + i] = u8(((5 - i) * a0 + i * a1) / 5);
        pal[6] = 0;
        pal[7] = 255;
    }
    u64 bits = 0;
    for (int i = 0; i < 6; ++i)
        bits |= u64(s[2 + i]) << (8 * i);
    for (int i = 0; i < 16; ++i)
        out[i] = pal[(bits >> (3 * i)) & 7];
}

enum class kind { bc1, bc2, bc3, bc4, bc5, none };

static kind classify(gli::format f)
{
    switch (f)
    {
    case gli::FORMAT_RGB_DXT1_UNORM_BLOCK8:
    case gli::FORMAT_RGB_DXT1_SRGB_BLOCK8:
    case gli::FORMAT_RGBA_DXT1_UNORM_BLOCK8:
    case gli::FORMAT_RGBA_DXT1_SRGB_BLOCK8: return kind::bc1;
    case gli::FORMAT_RGBA_DXT3_UNORM_BLOCK16:
    case gli::FORMAT_RGBA_DXT3_SRGB_BLOCK16: return kind::bc2;
    case gli::FORMAT_RGBA_DXT5_UNORM_BLOCK16:
    case gli::FORMAT_RGBA_DXT5_SRGB_BLOCK16: return kind::bc3;
    case gli::FORMAT_R_ATI1N_UNORM_BLOCK8: return kind::bc4;
    case gli::FORMAT_RG_ATI2N_UNORM_BLOCK16: return kind::bc5;
    default: return kind::none;
    }
}

// Decode one mip level (tightly packed blocks) into w*h RGBA8 pixels.
static void decode_level(kind k, const u8* src, int w, int h, u8* dst)
{
    const int bw = (w + 3) / 4, bh = (h + 3) / 4;
    const size_t block_size = (k == kind::bc1 || k == kind::bc4) ? 8 : 16;
    rgba color[16];
    u8 alpha[16], red[16], green[16];

    for (int by = 0; by < bh; ++by)
    {
        for (int bx = 0; bx < bw; ++bx)
        {
            const u8* b = src + (size_t(by) * bw + bx) * block_size;
            switch (k)
            {
            case kind::bc1: decode_color_block(b, color, true); break;
            case kind::bc2:
                decode_color_block(b + 8, color, false);
                for (int i = 0; i < 16; ++i) // explicit 4-bit alpha
                {
                    const u8 nib = u8((b[i / 2] >> ((i & 1) * 4)) & 0xF);
                    color[i].a = u8(nib * 17);
                }
                break;
            case kind::bc3:
                decode_color_block(b + 8, color, false);
                decode_alpha_block(b, alpha);
                for (int i = 0; i < 16; ++i)
                    color[i].a = alpha[i];
                break;
            case kind::bc4:
                decode_alpha_block(b, red);
                for (int i = 0; i < 16; ++i)
                    color[i] = { red[i], red[i], red[i], 255 };
                break;
            case kind::bc5:
                decode_alpha_block(b, red);
                decode_alpha_block(b + 8, green);
                for (int i = 0; i < 16; ++i)
                    color[i] = { red[i], green[i], 0, 255 };
                break;
            default: return;
            }
            for (int py = 0; py < 4; ++py)
            {
                const int y = by * 4 + py;
                if (y >= h)
                    break;
                for (int px = 0; px < 4; ++px)
                {
                    const int x = bx * 4 + px;
                    if (x >= w)
                        break;
                    const rgba& c = color[py * 4 + px];
                    u8* d = dst + (size_t(y) * w + x) * 4;
                    d[0] = c.r; d[1] = c.g; d[2] = c.b; d[3] = c.a;
                }
            }
        }
    }
}
} // namespace dxt

// Upload a DXT-compressed gli texture as decoded RGBA8. Returns 0 when the format/target
// isn't a DXT 2D/cube texture (caller falls through to the regular path). out_w/out_h
// report the uploaded base-level size (may be smaller than the file's — see mip-skip).
static GLuint ios_upload_dxt_as_rgba8(const gli::texture& texture, cpcstr fn,
                                      GLint& out_w, GLint& out_h)
{
    const dxt::kind k = dxt::classify(texture.format());
    if (k == dxt::kind::none)
        return 0;

    // Volume textures (water_sbumpvolume): decode each depth slice of each level.
    // A DXT 3D level stores its slices' 4x4-block data consecutively.
    if (texture.target() == gli::TARGET_3D)
    {
        while (glGetError() != GL_NO_ERROR)
            ;
        const glm::tvec3<GLsizei> e0(texture.extent());
        GLuint tex = 0;
        glGenTextures(1, &tex);
        glBindTexture(GL_TEXTURE_3D, tex);
        glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_BASE_LEVEL, 0);
        glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_MAX_LEVEL, static_cast<GLint>(texture.levels() - 1));
        glTexStorage3D(GL_TEXTURE_3D, static_cast<GLint>(texture.levels()), GL_RGBA8, e0.x, e0.y, e0.z);
        GLenum err3 = glGetError();
        if (err3 != GL_NO_ERROR)
            Msg("! OpenGL: 0x%x: iOS DXT->RGBA8 3D storage (%dx%dx%d) failed: '%s'",
                err3, e0.x, e0.y, e0.z, fn);
        xr_vector<u8> slice(size_t(e0.x) * e0.y * 4);
        const size_t block_bytes = (k == dxt::kind::bc1 || k == dxt::kind::bc4) ? 8 : 16;
        for (size_t level = 0; level < texture.levels(); ++level)
        {
            const glm::tvec3<GLsizei> e(texture.extent(level));
            const size_t slice_bytes = size_t((e.x + 3) / 4) * ((e.y + 3) / 4) * block_bytes;
            const u8* src = static_cast<const u8*>(texture.data(0, 0, level));
            for (GLsizei z = 0; z < e.z; ++z)
            {
                dxt::decode_level(k, src + slice_bytes * z, e.x, e.y, slice.data());
                glTexSubImage3D(GL_TEXTURE_3D, static_cast<GLint>(level), 0, 0, z,
                                e.x, e.y, 1, GL_RGBA, GL_UNSIGNED_BYTE, slice.data());
                err3 = glGetError();
                if (err3 != GL_NO_ERROR)
                    Msg("! OpenGL: 0x%x: iOS DXT->RGBA8 3D upload (level %zu slice %d) failed: '%s'",
                        err3, level, z, fn);
            }
        }
        out_w = e0.x;
        out_h = e0.y;
        return tex;
    }

    if (texture.target() != gli::TARGET_2D && texture.target() != gli::TARGET_CUBE)
    {
        Msg("! iOS DXT decode: unsupported target for '%s' — skipped", fn);
        return 0;
    }

    // Decoding DXT to RGBA8 inflates GPU memory 4-8x and Apple Silicon memory is unified,
    // so it all counts toward the ~few-GB jetsam limit — a full CoP level prefetch hit
    // 1.6 GB and the process was killed on the first world frame. Drop top mip levels
    // until the base fits 1024px: one skipped level = 4x less memory for that texture,
    // and the on-screen difference on a phone display is negligible. UI textures are
    // exempt — font atlases and HUD art are sampled by texel position and must keep
    // their authored size.
    constexpr GLsizei k_ios_tex_cap = 1024;
    size_t skip = 0;
    const bool size_sensitive = strstr(fn, "ui\\") || strstr(fn, "ui/");
    if (!size_sensitive)
    {
        while (skip + 1 < texture.levels())
        {
            const glm::tvec3<GLsizei> e(texture.extent(skip));
            if (e.x <= k_ios_tex_cap && e.y <= k_ios_tex_cap)
                break;
            ++skip;
        }
    }

    const size_t gl_levels = texture.levels() - skip;
    const glm::tvec3<GLsizei> ext0(texture.extent(skip));
    const GLenum target = texture.target() == gli::TARGET_CUBE ? GL_TEXTURE_CUBE_MAP : GL_TEXTURE_2D;

    // GL error flags are STICKY: whatever some earlier path left pending (the unported
    // video-texture wrapper was raising 0x500/0x506 around texture loads) would show up in
    // OUR check and mis-attribute the failure. Drain the queue first, then check per stage.
    while (glGetError() != GL_NO_ERROR)
        ;

    GLuint tex = 0;
    glGenTextures(1, &tex);
    glBindTexture(target, tex);
    glTexParameteri(target, GL_TEXTURE_BASE_LEVEL, 0);
    glTexParameteri(target, GL_TEXTURE_MAX_LEVEL, static_cast<GLint>(gl_levels - 1));
    glTexStorage2D(target, static_cast<GLint>(gl_levels), GL_RGBA8, ext0.x, ext0.y);
    GLenum err = glGetError();
    if (err != GL_NO_ERROR)
        Msg("! OpenGL: 0x%x: iOS DXT->RGBA8 storage (%dx%d, %zu levels) failed: '%s'",
            err, ext0.x, ext0.y, gl_levels, fn);

    xr_vector<u8> buf(size_t(ext0.x) * ext0.y * 4);
    for (size_t face = 0; face < texture.faces(); ++face)
    {
        for (size_t level = skip; level < texture.levels(); ++level)
        {
            const glm::tvec3<GLsizei> ext(texture.extent(level));
            dxt::decode_level(k, static_cast<const u8*>(texture.data(0, face, level)),
                              ext.x, ext.y, buf.data());
            const GLenum sub_target = texture.target() == gli::TARGET_CUBE
                ? static_cast<GLenum>(GL_TEXTURE_CUBE_MAP_POSITIVE_X + face)
                : target;
            glTexSubImage2D(sub_target, static_cast<GLint>(level - skip), 0, 0, ext.x, ext.y,
                            GL_RGBA, GL_UNSIGNED_BYTE, buf.data());
            err = glGetError();
            if (err != GL_NO_ERROR)
                Msg("! OpenGL: 0x%x: iOS DXT->RGBA8 upload (face %zu level %zu) failed: '%s'",
                    err, face, level, fn);
        }
    }
    out_w = ext0.x;
    out_h = ext0.y;
    return tex;
}
#endif // XR_PLATFORM_APPLE_IOS

void fix_texture_name(pstr fn)
{
    pstr _ext = strext(fn);
    if (_ext &&
        (0 == xr_stricmp(_ext, ".tga") ||
            0 == xr_stricmp(_ext, ".dds") ||
            0 == xr_stricmp(_ext, ".bmp") ||
            0 == xr_stricmp(_ext, ".ogm")))
        *_ext = 0;
}

int get_texture_load_lod(LPCSTR fn)
{
    CInifile::Sect& sect = pSettings->r_section("reduce_lod_texture_list");

    for (const auto& item : sect.Data)
    {
        if (strstr(fn, item.first.c_str()))
        {
            if (psTextureLOD < 1)
                return 0;
            if (psTextureLOD < 3)
                return 1;
            return 2;
        }
    }

    if (psTextureLOD < 2)
        return 0;
    if (psTextureLOD < 4)
        return 1;
    return 2;
}

u32 calc_texture_size(int lod, u32 mip_cnt, size_t orig_size)
{
    if (1 == mip_cnt)
        return orig_size;

    int _lod = lod;
    float res = float(orig_size);

    while (_lod > 0)
    {
        --_lod;
        res -= res / 1.333f;
    }
    return iFloor(res);
}

GLuint CRender::texture_load(LPCSTR fRName, u32& ret_msize, GLenum& ret_desc, GLint& ret_width, GLint& ret_height)
{
    // Level-0 extents are reported from the image itself: OpenGL ES 3.0 has no
    // glGetTexLevelParameteriv (it's 3.1+), so CTexture::desc_update can't query them
    // back from GL on device — the font renderer crashed to a NULL glad entry doing so.
    ret_width = ret_height = 0;
    ret_msize = 0;
    R_ASSERT1_CURE(fRName && fRName[0], { return 0; });

    GLuint pTexture = 0;
    string_path fn;
    {
        // make file name
        string_path fname;
        xr_strcpy(fname, fRName);
        fix_texture_name(fname);

        // Call to FS.exist WRITES to fn !

        if (!FS.exist(fn, "$game_textures$", fname, ".dds") && strstr(fname, "_bump"))
        {
            Msg("! Fallback to default bump map: %s", fname);
            if (strstr(fname, "_bump#"))
                R_ASSERT1_CURE(FS.exist(fn, "$game_textures$", "ed\\ed_dummy_bump#", ".dds"), return 0);
            else
                R_ASSERT1_CURE(FS.exist(fn, "$game_textures$", "ed\\ed_dummy_bump", ".dds"), return 0);
        }
        else
        {
            bool exist = false;

            for (cpcstr folder : { "$level$", "$game_saves$", "$game_textures$" })
            {
                exist = FS.exist(fn, folder, fname, ".dds");
                if (exist)
                    break;
            }

            if (!exist)
            {
                Msg("! Can't find texture '%s'", fname);
                if (!FS.exist(fn, "$game_textures$", "ed\\ed_not_existing_texture", ".dds"))
                    return 0;
            }
        }
    }

    // Load and get header
    IReader* S = FS.r_open(fn);
    R_ASSERT2_CURE(S, fn, { return 0; });
    size_t img_size = S->length();
#ifdef DEBUG
    Msg("* Loaded: %s[%d]b", fn, img_size);
#endif // DEBUG
    gli::texture texture = gli::load((char*)S->pointer(), img_size);
    R_ASSERT2(!texture.empty(), fn);

    u32 mip_cnt = u32(-1); // XXX: write to it when reading with GLI!

#if defined(XR_PLATFORM_APPLE_IOS)
    // DXT assets can't be uploaded on ES — decode to RGBA8 (see ios_upload_dxt_as_rgba8).
    if (const GLuint decoded = ios_upload_dxt_as_rgba8(texture, fn, ret_width, ret_height))
    {
        FS.r_close(S);
        xr_strlwr(fn);
        ret_desc = texture.target() == gli::TARGET_CUBE ? GL_TEXTURE_CUBE_MAP
            : texture.target() == gli::TARGET_3D ? GL_TEXTURE_3D
                                                 : GL_TEXTURE_2D;
        const int lod = is_target_cube(texture.target()) ? 0 : get_texture_load_lod(fn);
        ret_msize = calc_texture_size(lod, static_cast<u32>(texture.levels()), img_size);
        return decoded;
    }
    // Non-DXT path: translate with the ES profile so external formats/types are ES-legal
    // (ES 3.0 has no GL_BGRA upload; the ES profile maps BGRA8 to RGBA + texture swizzle).
    gli::gl GL(gli::gl::PROFILE_ES30);

    // Legacy uncompressed layouts (BGR8, luminance/alpha fonts, V8U8 water normals, ...)
    // translate to enums core ES 3.0 rejects — ~130 pfx/water/ui files died with 0x500 at
    // glTexStorage2D on device (no particles, no water). Whitelist the ES-legal externals
    // and CPU-convert everything else to plain RGBA8 (gli::convert unpacks any
    // uncompressed source). Log the original format id so leftovers stay diagnosable.
    if (!gli::is_compressed(texture.format()) && texture.format() != gli::FORMAT_RGBA8_UNORM_PACK8)
    {
        const gli::gl::format probe = GL.translate(texture.format(), texture.swizzles());
        const bool ext_ok = probe.External == gli::gl::EXTERNAL_RED
            || probe.External == gli::gl::EXTERNAL_RG
            || probe.External == gli::gl::EXTERNAL_RGB
            || probe.External == gli::gl::EXTERNAL_RGBA;
        const bool int_ok = probe.Internal == gli::gl::INTERNAL_R8_UNORM
            || probe.Internal == gli::gl::INTERNAL_RG8_UNORM
            || probe.Internal == gli::gl::INTERNAL_RGB8_UNORM
            || probe.Internal == gli::gl::INTERNAL_RGBA8_UNORM
            || probe.Internal == gli::gl::INTERNAL_SRGB8
            || probe.Internal == gli::gl::INTERNAL_SRGB8_ALPHA8
            || probe.Internal == gli::gl::INTERNAL_R5G6B5
            || probe.Internal == gli::gl::INTERNAL_RGB5A1
            || probe.Internal == gli::gl::INTERNAL_RGBA4;
        if (!ext_ok || !int_ok)
        {
            const int orig_fmt = int(texture.format());
            switch (texture.target())
            {
            case gli::TARGET_2D:
                texture = gli::convert(gli::texture2d(texture), gli::FORMAT_RGBA8_UNORM_PACK8);
                break;
            case gli::TARGET_CUBE:
                texture = gli::convert(gli::texture_cube(texture), gli::FORMAT_RGBA8_UNORM_PACK8);
                break;
            case gli::TARGET_3D:
                texture = gli::convert(gli::texture3d(texture), gli::FORMAT_RGBA8_UNORM_PACK8);
                break;
            default:
                break;
            }
            Msg("* iOS: converted legacy texture format %d (internal 0x%x external 0x%x) -> RGBA8: '%s'",
                orig_fmt, probe.Internal, probe.External, fn);
        }
    }
#else
    gli::gl GL(gli::gl::PROFILE_GL33);
#endif

    gli::gl::format const format = GL.translate(texture.format(), texture.swizzles());
    GLenum target = GL.translate(texture.target());

#if defined(XR_PLATFORM_APPLE_IOS)
    // GL error flags are STICKY. The DXT branch drains them before its checks; this path
    // did NOT — a single unchecked error from an earlier op (video path, sampler setup)
    // gets mis-attributed to the first normal-path texture that checks. The device logged
    // 129 identical "Invalid 2D texture" 0x500s here whose conversions never triggered —
    // drain first so a real failure is really ours, and make the message carry the format.
    while (glGetError() != GL_NO_ERROR)
        ;
#endif

    glGenTextures(1, &pTexture);
    glBindTexture(target, pTexture);

    glTexParameteri(target, GL_TEXTURE_BASE_LEVEL, 0);
    glTexParameteri(target, GL_TEXTURE_MAX_LEVEL, static_cast<GLint>(texture.levels() - 1));

    if (gli::gl::EXTERNAL_RED != format.External) // skip for proper greyscale-alpha font textures
    {
#if defined(XR_PLATFORM_APPLE_IOS)
        // The vector GL_TEXTURE_SWIZZLE_RGBA pname is desktop-GL only — OpenGL ES 3.0 has
        // just the per-channel pnames. The vector call raised GL_INVALID_ENUM on EVERY
        // normal-path texture (the sticky error behind all 129 phantom "Invalid 2D
        // texture" logs) and the swizzle silently never applied (BGRA assets showed with
        // red/blue swapped). Set the four channels individually — legal on both APIs.
        glTexParameteri(target, GL_TEXTURE_SWIZZLE_R, format.Swizzles[gli::SWIZZLE_RED]);
        glTexParameteri(target, GL_TEXTURE_SWIZZLE_G, format.Swizzles[gli::SWIZZLE_GREEN]);
        glTexParameteri(target, GL_TEXTURE_SWIZZLE_B, format.Swizzles[gli::SWIZZLE_BLUE]);
        glTexParameteri(target, GL_TEXTURE_SWIZZLE_A, format.Swizzles[gli::SWIZZLE_ALPHA]);
#else
        glTexParameteriv(target, GL_TEXTURE_SWIZZLE_RGBA, &format.Swizzles[gli::SWIZZLE_RED]);
#endif
    }

    glm::tvec3<GLsizei> const tex_extent(texture.extent());

    GLenum err;
    switch (texture.target())
    {
    case gli::TARGET_2D:
    case gli::TARGET_CUBE:
        glTexStorage2D(target, static_cast<GLint>(texture.levels()), format.Internal,
                       tex_extent.x, tex_extent.y);
        err = glGetError();
        if (err != GL_NO_ERROR)
        {
            VERIFY(err == GL_NO_ERROR);
            Msg("! OpenGL: 0x%x: Invalid 2D texture (gli fmt %d, int 0x%x ext 0x%x type 0x%x, compressed %d): '%s'",
                err, int(texture.format()), format.Internal, format.External, format.Type,
                int(gli::is_compressed(texture.format())), fn);
        }
        break;
    case gli::TARGET_3D:
    case gli::TARGET_CUBE_ARRAY:
        glTexStorage3D(target, static_cast<GLint>(texture.levels()), format.Internal,
                       tex_extent.x, tex_extent.y, tex_extent.z);
        err = glGetError();
        if (err != GL_NO_ERROR)
        {
            VERIFY(err == GL_NO_ERROR);
            Msg("! OpenGL: 0x%x: Invalid 3D texture: '%s'", err, fn);
        }
        break;
    default:
        NODEFAULT;
        break;
    }

    for (size_t layer = 0; layer < texture.layers(); ++layer)
    {
        for (size_t face = 0; face < texture.faces(); ++face)
        {
            for (size_t level = 0; level < texture.levels(); ++level)
            {
                glm::tvec3<GLsizei> const tex_level_extent(texture.extent(level));
                GLenum sub_target = gli::is_target_cube(texture.target())
                         ? static_cast<GLenum>(GL_TEXTURE_CUBE_MAP_POSITIVE_X + face)
                         : target;

                switch (texture.target())
                {
                case gli::TARGET_2D:
                case gli::TARGET_CUBE:
                {
                    if (gli::is_compressed(texture.format()))
                    {
                        glCompressedTexSubImage2D(sub_target, static_cast<GLint>(level),
                                    0, 0, tex_level_extent.x, tex_level_extent.y,
                                    format.Internal, static_cast<GLsizei>(texture.size(level)),
                                    texture.data(layer, face, level));
                        err = glGetError();
                        if (err != GL_NO_ERROR)
                        {
                            VERIFY(err == GL_NO_ERROR);
                            Msg("! OpenGL: 0x%x: Invalid 2D compressed subtexture: '%s'", err, fn);
                        }
                    }
                    else
                    {
                        glTexSubImage2D(sub_target, static_cast<GLint>(level),
                                    0, 0, tex_level_extent.x, tex_level_extent.y,
                                    format.External, format.Type,
                                    texture.data(layer, face, level));
                        err = glGetError();
                        if (err != GL_NO_ERROR)
                        {
                            VERIFY(err == GL_NO_ERROR);
                            Msg("! OpenGL: 0x%x: Invalid 2D subtexture: '%s'", err, fn);
                        }

                    }
                    break;
                }
                case gli::TARGET_3D:
                case gli::TARGET_CUBE_ARRAY:
                {
                    if (gli::is_compressed(texture.format()))
                    {
                        glCompressedTexSubImage3D(target, static_cast<GLint>(level),
                                    0, 0, 0, tex_level_extent.x, tex_level_extent.y, tex_level_extent.z,
                                    format.Internal, static_cast<GLsizei>(texture.size(level)),
                                    texture.data(layer, face, level));
                        err = glGetError();
                        if (err != GL_NO_ERROR)
                        {
                            VERIFY(err == GL_NO_ERROR);
                            Msg("! OpenGL: 0x%x: Invalid compressed 3D subtexture: '%s'", err, fn);
                        }
                    }
                    else
                    {
                        glTexSubImage3D(target, static_cast<GLint>(level),
                                    0, 0, 0, tex_level_extent.x, tex_level_extent.y, tex_level_extent.z,
                                    format.External, format.Type,
                                    texture.data(layer, face, level));
                        err = glGetError();
                        if (err != GL_NO_ERROR)
                        {
                            VERIFY(err == GL_NO_ERROR);
                            Msg("! OpenGL: 0x%x: Invalid 3D subtexture: '%s'", err, fn);
                        }
                    }
                    break;
                }
                default:
                    NODEFAULT;
                    break;
                }
            }
        }
    }

    FS.r_close(S);

    xr_strlwr(fn);
    ret_desc = target;
    ret_width = tex_extent.x;
    ret_height = tex_extent.y;
    int img_loaded_lod = is_target_cube(texture.target()) ? 0 : get_texture_load_lod(fn);
    ret_msize = calc_texture_size(img_loaded_lod, mip_cnt, img_size);
    return pTexture;
}
} // namespace xray::render::RENDER_NAMESPACE
