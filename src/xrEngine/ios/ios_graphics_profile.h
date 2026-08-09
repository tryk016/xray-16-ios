#pragma once

#include "xrCore/xr_token.h"

extern u32 psIOSGraphicsProfile;
extern const xr_token iosGraphicsProfileTokens[];

void ios_graphics_profile_on_frame(float workMilliseconds, float elapsedSeconds);
void ios_graphics_profile_before_frame();
void ios_graphics_profile_on_config_loaded();
