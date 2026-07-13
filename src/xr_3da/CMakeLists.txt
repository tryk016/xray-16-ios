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

if (NOT XRAY_PLATFORM_IOS)
    # The iOS app target is a MACOSX_BUNDLE built later (modeled on misc/ios/smoketest),
    # not this RUNTIME install of the desktop executable.
    install(TARGETS xr_3da RUNTIME
        DESTINATION "${CMAKE_INSTALL_BINDIR}"
    )
endif()
