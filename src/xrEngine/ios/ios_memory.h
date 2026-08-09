#pragma once

#include <cstdint>

namespace ios_memory
{
struct Snapshot
{
    bool valid{};
    std::uint64_t physicalFootprint{};
    std::uint64_t physicalFootprintPeak{};
    std::uint64_t resident{};
    std::uint64_t residentPeak{};
    std::uint64_t compressed{};
    std::uint64_t compressedPeak{};
};

Snapshot Capture();
void Log(const char* label, const Snapshot& snapshot);
} // namespace ios_memory
