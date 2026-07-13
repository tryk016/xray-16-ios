add_executable(xr_3da)

target_sources(xr_3da PRIVATE
    entry_point.cpp
    stdafx.h
)

if (NOT XRAY_PLATFORM_IOS)
    # Windows resource script + icons/splash — inert elsewhere, but keep them off the
    # iOS/Xcode target (the iOS app icon/launch assets come with the Phase 3 app bundle).
    target_sources(xr_3da PRIVATE
        resource.h
        resource.rc

        splash_oxr.bmp
        stalker_cop.ico
        stalker_cs.ico
        stalker_soc.ico
    )
endif()

set_property(
    DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
    PROPERTY
    VS_STARTUP_PROJECT xr_3da
)

target_include_directories(xr_3da
    PRIVATE
    "${CMAKE_SOURCE_DIR}/src"
)

target_link_libraries(xr_3da
    PRIVATE
    xrCore
    xrAPI
    xrEngine
    xrRender_GL
    xrGame
)

set_target_properties(xr_3da PROPERTIES
    PREFIX ""
)

if (XRAY_PLATFORM_IOS)
    # SDL2main supplies the iOS UIApplicationMain entry that calls our SDL_main
    # (entry_point.cpp renames main -> SDL_main via <SDL_main.h> on iOS).
    if (NOT TARGET SDL2::SDL2main)
        message(FATAL_ERROR "SDL2::SDL2main target not found — required for the iOS SDL_main entry point")
    endif()
    target_link_libraries(xr_3da PRIVATE SDL2::SDL2main)
endif()

if (NOT XRAY_PLATFORM_IOS)
    # The iOS app target is a MACOSX_BUNDLE built later (modeled on misc/ios/smoketest),
    # not this RUNTIME install of the desktop executable.
    install(TARGETS xr_3da RUNTIME
        DESTINATION "${CMAKE_INSTALL_BINDIR}"
    )
endif()
