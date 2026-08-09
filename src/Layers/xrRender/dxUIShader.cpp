#include "stdafx.h"
#include "dxUIShader.h"

namespace xray::render::RENDER_NAMESPACE
{
void dxUIShader::Copy(IUIShader& _in) { *this = *((dxUIShader*)&_in); }
void dxUIShader::create(LPCSTR sh, LPCSTR tex) { hShader.create(sh, tex); }
void dxUIShader::destroy() { hShader.destroy(); }

bool dxUIShader::operator==(const IUIShader& other) const
{
    return hShader == static_cast<const dxUIShader&>(other).hShader;
}

CTexture* dxUIShader::GetBaseTexture() const
{
    if (!hShader)
        return nullptr;

    const SPass& pass = *hShader->E[0]->passes[0];
    if (!pass.T)
        return nullptr;

    const STextureList& textures = *pass.T;
    if (textures.empty())
        return nullptr;

    const R_constant* sbase = pass.constants->get(baseTexture)._get();
    if (!sbase)
        return textures.front().second._get();

    // STextureList is a sorted vector of (texture unit, texture) pairs; the
    // sampler unit is not an index into that vector.  This happened to work
    // while s_base was assigned unit 0, but linked GLSL ES programs can assign
    // it unit 1 or later (for example, screen_res is the first active uniform
    // in the UI shader).  Indexing textures[samp.index] then read past the
    // single-element UI texture list, reported a 0x0 atlas and produced
    // infinite UVs for every textured HUD/menu/inventory quad.
    for (const auto& [stage, texture] : textures)
    {
        if (stage == sbase->samp.index)
            return texture._get();
    }

    return nullptr;
}

xrImTextureData dxUIShader::GetImGuiTextureId()
{
    const auto texture = GetBaseTexture();
    if (!texture)
        return {};

    texture->m_last_used_frame = Device.dwFrame;
    return
    {
        texture->GetImTextureID(),
        {
            (float)texture->get_Width(),
            (float)texture->get_Height()
        }
    };
}

bool dxUIShader::GetBaseTextureResolution(Fvector2& res)
{
    const auto texture = GetBaseTexture();
    if (!texture)
    {
        res = {};
        return false;
    }

    res = { float(texture->get_Width()), float(texture->get_Height()) };
    return true;
}
} // namespace xray::render::RENDER_NAMESPACE
