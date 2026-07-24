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
    # Version code: SideStore decides "is there an update?" purely from
    # CFBundleShortVersionString, so it MUST strictly increase every build. XRAY_BUILD_ID
    # alone is date-derived (it only changes once per calendar day), so two builds on the
    # same day produce the SAME version and SideStore shows no update. When CI passes its
    # monotonic run number (-DXR_IOS_BUILD_NUMBER=${github.run_number}), fold it in:
    #   code = XRAY_BUILD_ID * 1000 + run_number
    # Both terms are non-decreasing over time and run_number strictly increases every run,
    # so the sum strictly increases per build; the *1000 base keeps it well above any
    # date-only build we shipped before (e.g. 10018 -> 10018034), so updates are always
    # detected as newer. Local dev builds (no CI number) fall back to the raw build id.
    if (DEFINED XR_IOS_BUILD_NUMBER AND NOT "${XR_IOS_BUILD_NUMBER}" STREQUAL "")
        math(EXPR XR_IOS_VERSION_CODE "${XRAY_BUILD_ID} * 1000 + ${XR_IOS_BUILD_NUMBER}")
    else()
        set(XR_IOS_VERSION_CODE "${XRAY_BUILD_ID}")
    endif()
    set(XR_IOS_SHORT_VERSION "1.6.02.${XR_IOS_VERSION_CODE}")
    set(XR_IOS_BUILD_VERSION "${XR_IOS_VERSION_CODE}")
    set(XR_IOS_MIN_OS        "${CMAKE_OSX_DEPLOYMENT_TARGET}")
    configure_file("${CMAKE_SOURCE_DIR}/misc/ios/Info.plist.in"
                   "${CMAKE_CURRENT_BINARY_DIR}/Info.plist" @ONLY)
    set_target_properties(xr_3da PROPERTIES
        MACOSX_BUNDLE TRUE
        MACOSX_BUNDLE_INFO_PLIST "${CMAKE_CURRENT_BINARY_DIR}/Info.plist"
        XCODE_ATTRIBUTE_PRODUCT_BUNDLE_IDENTIFIER "${XR_IOS_BUNDLE_ID}"
        XCODE_ATTRIBUTE_TARGETED_DEVICE_FAMILY "1,2"
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
