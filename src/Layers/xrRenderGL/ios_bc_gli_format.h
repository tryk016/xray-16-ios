#pragma once

#include "ios_bc_texture_codec.h"

#include <gli/gli.hpp>

namespace xray::render::ios_bc
{
// This is the only GLI -> iOS BC fallback mapping.  Keep unsupported formats as
// Kind::none so the existing non-DXT path remains the single fallback owner.
constexpr FormatInfo FromGliFormat(const gli::format format) noexcept
{
    switch (format)
    {
    case gli::FORMAT_RGB_DXT1_UNORM_BLOCK8:
    case gli::FORMAT_RGBA_DXT1_UNORM_BLOCK8:
        return { Kind::bc1, Transfer::linear, Layout::rgba };
    case gli::FORMAT_RGB_DXT1_SRGB_BLOCK8:
    case gli::FORMAT_RGBA_DXT1_SRGB_BLOCK8:
        return { Kind::bc1, Transfer::srgb, Layout::rgba };
    case gli::FORMAT_RGBA_DXT3_UNORM_BLOCK16:
        return { Kind::bc2, Transfer::linear, Layout::rgba };
    case gli::FORMAT_RGBA_DXT3_SRGB_BLOCK16:
        return { Kind::bc2, Transfer::srgb, Layout::rgba };
    case gli::FORMAT_RGBA_DXT5_UNORM_BLOCK16:
        return { Kind::bc3, Transfer::linear, Layout::rgba };
    case gli::FORMAT_RGBA_DXT5_SRGB_BLOCK16:
        return { Kind::bc3, Transfer::srgb, Layout::rgba };
    case gli::FORMAT_R_ATI1N_UNORM_BLOCK8:
        return { Kind::bc4, Transfer::linear, Layout::rrr1 };
    case gli::FORMAT_RG_ATI2N_UNORM_BLOCK16:
        return { Kind::bc5, Transfer::linear, Layout::rg01 };
    default:
        return {};
    }
}
} // namespace xray::render::ios_bc
