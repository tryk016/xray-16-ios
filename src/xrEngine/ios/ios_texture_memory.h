#pragma once

#include <algorithm>
#include <cstdint>

namespace ios_texture_memory
{
// Immutable RGBA8 storage consumed by a complete mip chain. This is the
// allocation uploaded to GL-on-Metal, not the much smaller BC/DXT source file.
constexpr std::uint64_t Rgba8MipChainBytes(std::uint32_t width, std::uint32_t height,
    std::uint32_t depth, const std::uint32_t levels, const std::uint32_t faces = 1,
    const std::uint32_t layers = 1)
{
    std::uint64_t bytes = 0;
    for (std::uint32_t level = 0; level < levels; ++level)
    {
        bytes += std::uint64_t(width) * height * depth * 4 * faces * layers;
        width = std::max(1u, width / 2);
        height = std::max(1u, height / 2);
        depth = std::max(1u, depth / 2);
    }
    return bytes;
}
} // namespace ios_texture_memory
