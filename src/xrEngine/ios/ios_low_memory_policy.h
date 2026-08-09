#pragma once

namespace ios_low_memory
{
struct Decision
{
    bool trimCpu{};
    bool evictGpu{};
};

class Policy
{
public:
    Decision Update(const bool warning, const bool foreground, const bool deviceReady,
        const bool primaryContext, const bool rendering)
    {
        if (warning)
            gpuEvictionPending = true;

        const bool canEvict = gpuEvictionPending && foreground && deviceReady && primaryContext && !rendering;
        if (canEvict)
            gpuEvictionPending = false;
        return { warning, canEvict };
    }

    void DeferGpuEviction() { gpuEvictionPending = true; }
    bool GpuEvictionPending() const { return gpuEvictionPending; }

private:
    bool gpuEvictionPending{};
};
} // namespace ios_low_memory
