#pragma once

#include <cstdint>

namespace ios_texture_eviction
{
inline constexpr std::uint64_t BudgetBytes = 256ull * 1024 * 1024;
inline constexpr std::uint32_t MinimumUnusedFrames = 300;

constexpr bool IsCandidate(const bool loaded, const bool user, const bool pinned,
    const std::uint32_t bytes, const std::uint32_t currentFrame, const std::uint32_t lastUsedFrame)
{
    return loaded && !user && !pinned && bytes != 0
        && currentFrame - lastUsedFrame >= MinimumUnusedFrames;
}

constexpr bool FitsBudget(const std::uint64_t released, const std::uint32_t candidateBytes)
{
    return released + candidateBytes <= BudgetBytes;
}
} // namespace ios_texture_eviction
