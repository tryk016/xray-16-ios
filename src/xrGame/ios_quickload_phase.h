#pragma once

#include "xrCore/xr_types.h"

#if defined(XR_PLATFORM_APPLE_IOS)
enum class IosQuickLoadPhase
{
    Deferred,
    EventBegin,
    ObjectsRemoved,
    RestartBegin,
    OldAlifeDestroyed,
    IdsCleared,
    AlifeConstructBegin,
    NewAlifeConstructed,
    PrecacheComplete,
    RestartComplete,
    EventComplete,
};

u64 ios_quickload_phase_begin_request();
void ios_quickload_phase_emit(u64 request, IosQuickLoadPhase phase);
#endif
