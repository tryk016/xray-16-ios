# OpenXRay iOS third-party dependency superbuild.
#
# Cross-builds the engine's C dependencies from source for a single iOS SDK/arch using
# the vendored leetal toolchain, and installs them (static libs + CMake config / .pc /
# headers) into one prefix. The engine build (Phase 2d) then points find_package at that
# prefix via CMAKE_FIND_ROOT_PATH / CMAKE_PREFIX_PATH.
#
# This orchestrator is language-NONE: it does not compile anything itself, it only drives
# per-dependency sub-builds via ExternalProject, forwarding the iOS toolchain to each.
#
# Invoke once per SDK, e.g.:
#   cmake -S cmake/ios/deps -B build/ios-deps-iphoneos \
#     -DIOS_TOOLCHAIN_FILE=$PWD/cmake/toolchains/ios.toolchain.cmake \
#     -DPLATFORM=OS64 -DDEPLOYMENT_TARGET=15.0 -DENABLE_BITCODE=OFF \
#     -DCMAKE_INSTALL_PREFIX=$PWD/build/ios-prefix-iphoneos
#   cmake --build build/ios-deps-iphoneos --parallel
#
# PLATFORM: OS64 (arm64 device) | SIMULATORARM64 (arm64 simulator).

cmake_minimum_required(VERSION 3.23)

project(OpenXRayIOSDeps LANGUAGES NONE)

include(ExternalProject)

# ---- Required / defaulted inputs ------------------------------------------------------
foreach(_req IOS_TOOLCHAIN_FILE PLATFORM)
    if(NOT DEFINED ${_req})
        message(FATAL_ERROR "Set -D${_req}=... (see the header of this file).")
    endif()
endforeach()

if(NOT DEFINED DEPLOYMENT_TARGET)
    set(DEPLOYMENT_TARGET 15.0)
endif()
if(NOT DEFINED ENABLE_BITCODE)
    set(ENABLE_BITCODE OFF)
endif()
if(NOT IS_ABSOLUTE "${IOS_TOOLCHAIN_FILE}")
    message(FATAL_ERROR "IOS_TOOLCHAIN_FILE must be an absolute path.")
endif()

get_filename_component(_prefix "${CMAKE_INSTALL_PREFIX}" ABSOLUTE)
message(STATUS "iOS deps: PLATFORM=${PLATFORM} DEPLOYMENT_TARGET=${DEPLOYMENT_TARGET} prefix=${_prefix}")

# Forwarded to every dependency sub-build so all deps target the same SDK/arch/config.
set(_dep_common_args
    -DCMAKE_TOOLCHAIN_FILE=${IOS_TOOLCHAIN_FILE}
    -DPLATFORM=${PLATFORM}
    -DDEPLOYMENT_TARGET=${DEPLOYMENT_TARGET}
    -DENABLE_BITCODE=${ENABLE_BITCODE}
    -DCMAKE_INSTALL_PREFIX=${_prefix}
    -DCMAKE_PREFIX_PATH=${_prefix}
    -DCMAKE_BUILD_TYPE=Release
    -DBUILD_SHARED_LIBS=OFF
)

# ---- SDL2 -----------------------------------------------------------------------------
# The window / input / GL-context layer. First-class iOS support in SDL2's own CMake.
ExternalProject_Add(dep_SDL2
    URL      https://github.com/libsdl-org/SDL/releases/download/release-2.32.10/SDL2-2.32.10.tar.gz
    URL_HASH SHA256=5f5993c530f084535c65a6879e9b26ad441169b3e25d789d83287040a9ca5165
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    CMAKE_ARGS
        ${_dep_common_args}
        -DSDL_SHARED=OFF
        -DSDL_STATIC=ON
        -DSDL_TEST=OFF
        -DSDL2_DISABLE_INSTALL=OFF
    BUILD_ALWAYS OFF
)
