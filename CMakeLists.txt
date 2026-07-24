cmake_minimum_required(VERSION 3.23)

if(CMAKE_TOOLCHAIN_FILE MATCHES "ios\\.toolchain\\.cmake$" AND NOT DEFINED DEPLOYMENT_TARGET)
    set(DEPLOYMENT_TARGET 16.4 CACHE STRING "Minimum supported iOS version")
endif()

list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")
include(PreProjectInit)

calculate_xray_build_id(XRAY_BUILD_ID)
project(OpenXRay
    DESCRIPTION "OpenXRay is an improved version of the X-Ray Engine, \
                the game engine used in the world-famous S.T.A.L.K.E.R. game series by GSC Game World."
    VERSION 1.6.02.${XRAY_BUILD_ID}
    HOMEPAGE_URL "https://github.com/OpenXRay/xray-16"
    LANGUAGES CXX C
)

option(BUILD_SHARED_LIBS "Build shared libraries"     ${BUILD_SHARED_LIBS_DEFAULT_VALUE})

option(XRAY_USE_LUAJIT   "Use LuaJIT"                 ON)
option(XRAY_USE_ASAN     "Use AddressSanitizer"       OFF)
option(XRAY_ENABLE_TRACY "Enable tracy profiler"      OFF)

include(XRay.Build)
if (NOT XRAY_PLATFORM_IOS)
    # DEB/RPM + CPack packaging is desktop-Linux only; irrelevant to a sideloaded .ipa.
    include(XRay.Packaging)
endif()

add_subdirectory(Externals)
add_subdirectory(src)
add_subdirectory(res)
add_subdirectory(misc)

# CMake's Xcode generator emits GCC_GENERATE_DEBUGGING_SYMBOLS on each target,
# so a project-wide CMAKE_XCODE_ATTRIBUTE_* cache value does not override the
# generated Release default. Apply an explicit device/simulator choice to every
# compiled target after the complete graph exists. This is required for dsymutil
# to collect debug data from the static engine libraries, not only entry_point.
if (XRAY_PLATFORM_IOS AND CMAKE_GENERATOR STREQUAL "Xcode")
    function(xr_apply_ios_xcode_debug_attributes directory)
        get_property(_targets DIRECTORY "${directory}" PROPERTY BUILDSYSTEM_TARGETS)
        foreach(_target IN LISTS _targets)
            get_target_property(_imported "${_target}" IMPORTED)
            get_target_property(_type "${_target}" TYPE)
            if (NOT _imported AND
                _type MATCHES "^(EXECUTABLE|STATIC_LIBRARY|SHARED_LIBRARY|MODULE_LIBRARY|OBJECT_LIBRARY)$")
                if (DEFINED CMAKE_XCODE_ATTRIBUTE_GCC_GENERATE_DEBUGGING_SYMBOLS)
                    set_property(TARGET "${_target}" PROPERTY
                        XCODE_ATTRIBUTE_GCC_GENERATE_DEBUGGING_SYMBOLS
                        "${CMAKE_XCODE_ATTRIBUTE_GCC_GENERATE_DEBUGGING_SYMBOLS}")
                endif()
                if (DEFINED CMAKE_XCODE_ATTRIBUTE_DEBUG_INFORMATION_FORMAT)
                    set_property(TARGET "${_target}" PROPERTY
                        XCODE_ATTRIBUTE_DEBUG_INFORMATION_FORMAT
                        "${CMAKE_XCODE_ATTRIBUTE_DEBUG_INFORMATION_FORMAT}")
                endif()
            endif()
        endforeach()

        get_property(_subdirectories DIRECTORY "${directory}" PROPERTY SUBDIRECTORIES)
        foreach(_subdirectory IN LISTS _subdirectories)
            xr_apply_ios_xcode_debug_attributes("${_subdirectory}")
        endforeach()
    endfunction()

    xr_apply_ios_xcode_debug_attributes("${CMAKE_SOURCE_DIR}")
endif()
