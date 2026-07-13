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

    # iOS app bundle Info.plist (installability + Files-app log access). See misc/ios/Info.plist.in.
    set(XR_IOS_EXECUTABLE    "xr_3da")
    set(XR_IOS_BUNDLE_ID     "io.github.tryk016.openxray")
    set(XR_IOS_DISPLAY_NAME  "OpenXRay")
    # Append the git build id so the version increments per build — SideStore uses
    # CFBundleShortVersionString to detect updates (and to match the source's version).
    set(XR_IOS_SHORT_VERSION "${PROJECT_VERSION_MAJOR}.${PROJECT_VERSION_MINOR}.${PROJECT_VERSION_PATCH}.${XRAY_BUILD_ID}")
    set(XR_IOS_BUILD_VERSION "${XRAY_BUILD_ID}")
    set(XR_IOS_MIN_OS        "${CMAKE_OSX_DEPLOYMENT_TARGET}")
    configure_file("${CMAKE_SOURCE_DIR}/misc/ios/Info.plist.in"
                   "${CMAKE_CURRENT_BINARY_DIR}/Info.plist" @ONLY)
    set_target_properties(xr_3da PROPERTIES
        MACOSX_BUNDLE TRUE
        MACOSX_BUNDLE_INFO_PLIST "${CMAKE_CURRENT_BINARY_DIR}/Info.plist"
    )

    # App icon: add the asset catalog as a source so Xcode's actool compiles it into the
    # bundle and injects CFBundleIcons; APPICON_NAME selects the AppIcon set.
    target_sources(xr_3da PRIVATE "${CMAKE_SOURCE_DIR}/misc/ios/Assets.xcassets")
    set_target_properties(xr_3da PROPERTIES
        XCODE_ATTRIBUTE_ASSETCATALOG_COMPILER_APPICON_NAME "AppIcon"
    )

    # Bundle fsgame.ltx + base gamedata (configs/scripts/shaders, ~3.4 MB) into the .app.
    # On first launch the engine (LocatorAPI iOS branch) copies these into the writable
    # Documents dir and runs from there; the user adds real Call of Pripyat gamedata there.
    add_custom_command(TARGET xr_3da POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy
            "${CMAKE_SOURCE_DIR}/res/fsgame.ltx" "$<TARGET_BUNDLE_DIR:xr_3da>/fsgame.ltx"
        COMMAND ${CMAKE_COMMAND} -E copy_directory
            "${CMAKE_SOURCE_DIR}/res/gamedata" "$<TARGET_BUNDLE_DIR:xr_3da>/gamedata"
        COMMENT "iOS: bundling fsgame.ltx + base gamedata into the app"
        VERBATIM
    )
endif()

if (NOT XRAY_PLATFORM_IOS)
    # The iOS app target is a MACOSX_BUNDLE built later (modeled on misc/ios/smoketest),
    # not this RUNTIME install of the desktop executable.
    install(TARGETS xr_3da RUNTIME
        DESTINATION "${CMAKE_INSTALL_BINDIR}"
    )
endif()
