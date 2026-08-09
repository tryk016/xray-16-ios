#include "stdafx.h"

#include "ios_memory.h"

#include <mach/mach.h>

namespace ios_memory
{
Snapshot Capture()
{
    task_vm_info_data_t info{};
    mach_msg_type_number_t count = TASK_VM_INFO_COUNT;
    if (task_info(mach_task_self(), TASK_VM_INFO, reinterpret_cast<task_info_t>(&info), &count) != KERN_SUCCESS)
        return {};

    Snapshot snapshot;
    snapshot.valid = true;
    snapshot.resident = info.resident_size;
    snapshot.residentPeak = info.resident_size_peak;
    snapshot.compressed = info.compressed;
    snapshot.compressedPeak = info.compressed_peak;
    if (count >= TASK_VM_INFO_REV1_COUNT)
        snapshot.physicalFootprint = info.phys_footprint;
    if (count >= TASK_VM_INFO_REV3_COUNT && info.ledger_phys_footprint_peak > 0)
        snapshot.physicalFootprintPeak = static_cast<std::uint64_t>(info.ledger_phys_footprint_peak);
    return snapshot;
}

void Log(const char* label, const Snapshot& snapshot)
{
    if (!snapshot.valid)
    {
        Msg("! iOS memory %s: TASK_VM_INFO unavailable", label);
        return;
    }

    Msg("* iOS memory %s: phys_current=%llu K phys_peak=%llu K resident_current=%llu K "
        "resident_peak=%llu K compressed_current=%llu K compressed_peak=%llu K",
        label,
        static_cast<unsigned long long>(snapshot.physicalFootprint / 1024),
        static_cast<unsigned long long>(snapshot.physicalFootprintPeak / 1024),
        static_cast<unsigned long long>(snapshot.resident / 1024),
        static_cast<unsigned long long>(snapshot.residentPeak / 1024),
        static_cast<unsigned long long>(snapshot.compressed / 1024),
        static_cast<unsigned long long>(snapshot.compressedPeak / 1024));
}
} // namespace ios_memory
