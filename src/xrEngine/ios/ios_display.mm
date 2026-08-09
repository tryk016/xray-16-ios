// iOS OpenGL drawable sizing bridge.
//
// SDL's UIKit backend creates its EAGL view at contentScaleFactor 1 unless the
// all-or-nothing SDL_WINDOW_ALLOW_HIGHDPI flag is used. On the current iPhone
// that flag selects the screen's 3x nativeScale, while OpenXRay's performance
// target is exactly 2x (1864x860 for a 932x430 UIKit window). Set the actual
// EAGL view to 2x after SDL creates the context; SDL_GL_GetDrawableSize then
// reports the real backing dimensions and Present() becomes a 1:1 blit.

#include "ios_display.h"

#include <SDL.h>
#include <SDL_syswm.h>

#import <Foundation/Foundation.h>
#import <QuartzCore/CAEAGLLayer.h>
#import <UIKit/UIKit.h>

namespace ios_display
{
bool set_opengl_drawable_scale(SDL_Window* window, const float scale)
{
    if (!window || scale <= 0.0f)
        return false;

    @autoreleasepool
    {
        SDL_SysWMinfo info;
        SDL_VERSION(&info.version);
        if (!SDL_GetWindowWMInfo(window, &info) || info.subsystem != SDL_SYSWM_UIKIT)
            return false;

        UIWindow* uiWindow = info.info.uikit.window;
        UIView* view = uiWindow.rootViewController.view;
        if (!view || ![view.layer isKindOfClass:[CAEAGLLayer class]])
            return false;

        view.contentScaleFactor = static_cast<CGFloat>(scale);
        [view setNeedsLayout];
        [view layoutIfNeeded];
        return true;
    }
}

ThermalState thermal_state()
{
    switch ([NSProcessInfo processInfo].thermalState)
    {
    case NSProcessInfoThermalStateFair:
        return ThermalState::Fair;
    case NSProcessInfoThermalStateSerious:
        return ThermalState::Serious;
    case NSProcessInfoThermalStateCritical:
        return ThermalState::Critical;
    case NSProcessInfoThermalStateNominal:
    default:
        return ThermalState::Nominal;
    }
}

bool low_power_mode_enabled() { return [NSProcessInfo processInfo].lowPowerModeEnabled; }
} // namespace ios_display
