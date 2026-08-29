#include "StdAfx.h"

#include "ios_quickload_phase.h"

#if defined(XR_PLATFORM_APPLE_IOS)
#include "xrEngine/defines.h"
#include "xrEngine/device.h"
#include "xrEngine/ios/ios_memory.h"

#include <limits>
#include <unistd.h>

namespace
{
u64 g_nextQuickLoadRequest{};

const char* phase_name(IosQuickLoadPhase phase)
{
    switch (phase)
    {
    case IosQuickLoadPhase::Deferred:
        return "deferred";
    case IosQuickLoadPhase::EventBegin:
        return "event_begin";
    case IosQuickLoadPhase::ObjectsRemoved:
        return "objects_removed";
    case IosQuickLoadPhase::RestartBegin:
        return "restart_begin";
    case IosQuickLoadPhase::OldAlifeDestroyed:
        return "old_alife_destroyed";
    case IosQuickLoadPhase::IdsCleared:
        return "ids_cleared";
    case IosQuickLoadPhase::AlifeConstructBegin:
        return "alife_construct_begin";
    case IosQuickLoadPhase::NewAlifeConstructed:
        return "new_alife_constructed";
    case IosQuickLoadPhase::PrecacheComplete:
        return "precache_complete";
    case IosQuickLoadPhase::RestartComplete:
        return "restart_complete";
    case IosQuickLoadPhase::EventComplete:
        return "event_complete";
    }

    NODEFAULT;
    return "invalid";
}

bool captures_memory(IosQuickLoadPhase phase)
{
    switch (phase)
    {
    case IosQuickLoadPhase::EventBegin:
    case IosQuickLoadPhase::ObjectsRemoved:
    case IosQuickLoadPhase::OldAlifeDestroyed:
    case IosQuickLoadPhase::AlifeConstructBegin:
    case IosQuickLoadPhase::NewAlifeConstructed:
    case IosQuickLoadPhase::PrecacheComplete:
    case IosQuickLoadPhase::RestartComplete:
        return true;
    case IosQuickLoadPhase::Deferred:
    case IosQuickLoadPhase::RestartBegin:
    case IosQuickLoadPhase::IdsCleared:
    case IosQuickLoadPhase::EventComplete:
        return false;
    }

    NODEFAULT;
    return false;
}
} // namespace

u64 ios_quickload_phase_begin_request()
{
    if (psIOSDiagnostics != 1 || g_nextQuickLoadRequest == std::numeric_limits<u64>::max())
        return 0;

    return ++g_nextQuickLoadRequest;
}

void ios_quickload_phase_emit(u64 request, IosQuickLoadPhase phase)
{
    if (psIOSDiagnostics != 1 || request == 0)
        return;

    const pid_t process_id = getpid();
    if (process_id <= 0)
        return;

    ios_memory::Snapshot snapshot{};
    const bool capture_memory = captures_memory(phase);
    if (capture_memory)
        snapshot = ios_memory::Capture();

    const char* memory_kind = "none";
    if (capture_memory)
        memory_kind = snapshot.valid ? "task_vm_info" : "unavailable";

    const u64 physical_current = snapshot.valid ? snapshot.physicalFootprint / 1024 : 0;
    const u64 physical_peak = snapshot.valid ? snapshot.physicalFootprintPeak / 1024 : 0;
    const u64 resident_current = snapshot.valid ? snapshot.resident / 1024 : 0;
    const u64 resident_peak = snapshot.valid ? snapshot.residentPeak / 1024 : 0;
    const u64 compressed_current = snapshot.valid ? snapshot.compressed / 1024 : 0;
    const u64 compressed_peak = snapshot.valid ? snapshot.compressedPeak / 1024 : 0;

    Msg("* iOS quickload phase v1 pid=%d request=%llu frame=%u phase=%s memory=%s "
        "phys_current_k=%llu phys_peak_k=%llu resident_current_k=%llu resident_peak_k=%llu "
        "compressed_current_k=%llu compressed_peak_k=%llu",
        static_cast<int>(process_id),
        static_cast<unsigned long long>(request),
        Device.dwFrame,
        phase_name(phase),
        memory_kind,
        static_cast<unsigned long long>(physical_current),
        static_cast<unsigned long long>(physical_peak),
        static_cast<unsigned long long>(resident_current),
        static_cast<unsigned long long>(resident_peak),
        static_cast<unsigned long long>(compressed_current),
        static_cast<unsigned long long>(compressed_peak));
}
#endif
