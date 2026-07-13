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
# CMAKE_FIND_ROOT_PATH lets an inter-dependent lib (e.g. vorbis) find an already-installed
# one (ogg) inside our prefix; the leetal toolchain appends the SDK sysroot to it, and its
# FIND_ROOT_PATH modes default to BOTH so CMAKE_PREFIX_PATH is honoured too.
set(_dep_common_args
    -DCMAKE_TOOLCHAIN_FILE=${IOS_TOOLCHAIN_FILE}
    -DPLATFORM=${PLATFORM}
    -DDEPLOYMENT_TARGET=${DEPLOYMENT_TARGET}
    -DENABLE_BITCODE=${ENABLE_BITCODE}
    -DCMAKE_INSTALL_PREFIX=${_prefix}
    -DCMAKE_PREFIX_PATH=${_prefix}
    -DCMAKE_FIND_ROOT_PATH=${_prefix}
    -DCMAKE_BUILD_TYPE=Release
    -DBUILD_SHARED_LIBS=OFF
    # CMake 4.x removed compatibility with cmake_minimum_required(VERSION < 3.5).
    # libvorbis (2.8.12) and lzo (3.0) still request older minimums; this lets them
    # configure. Harmless for deps that already require >= 3.5 (SDL2, ogg).
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5
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

# ---- libogg ---------------------------------------------------------------------------
# Ogg container; dependency of both vorbis and theora. Ships a proper CMake build and
# installs an Ogg CMake package config that vorbis then consumes.
ExternalProject_Add(dep_libogg
    URL      https://github.com/xiph/ogg/releases/download/v1.3.6/libogg-1.3.6.tar.gz
    URL_HASH SHA256=83e6704730683d004d20e21b8f7f55dcb3383cdf84c0daedf30bde175f774638
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    CMAKE_ARGS
        ${_dep_common_args}
        -DBUILD_TESTING=OFF
        -DINSTALL_DOCS=OFF
    BUILD_ALWAYS OFF
)

# ---- libvorbis ------------------------------------------------------------------------
# Vorbis audio codec. find_package(Ogg) resolves to the libogg we installed above.
ExternalProject_Add(dep_libvorbis
    URL      https://github.com/xiph/vorbis/releases/download/v1.3.7/libvorbis-1.3.7.tar.gz
    URL_HASH SHA256=0e982409a9c3fc82ee06e08205b1355e5c6aa4c36bca58146ef399621b0ce5ab
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    CMAKE_ARGS
        ${_dep_common_args}
        -DBUILD_TESTING=OFF
    DEPENDS dep_libogg
    BUILD_ALWAYS OFF
)

# ---- libtheora ------------------------------------------------------------------------
# Theora video (menu/intro .ogv playback). Upstream is autotools-only and does not
# cross-compile cleanly for iOS, so we inject our own CMake build (cmake/ios/theora)
# over the source tree and build the portable, no-asm source set. Needs ogg headers.
ExternalProject_Add(dep_theora
    URL      https://downloads.xiph.org/releases/theora/libtheora-1.1.1.tar.gz
    URL_HASH SHA256=40952956c47811928d1e7922cda3bc1f427eb75680c3c37249c91e949054916b
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    PATCH_COMMAND ${CMAKE_COMMAND} -E copy
        ${CMAKE_CURRENT_SOURCE_DIR}/../theora/CMakeLists.txt <SOURCE_DIR>/CMakeLists.txt
    CMAKE_ARGS ${_dep_common_args}
    DEPENDS dep_libogg
    BUILD_ALWAYS OFF
)

# ---- lzo2 -----------------------------------------------------------------------------
# LZO compression (used by the VFS). lzo-2.10 ships a CMake build; installs liblzo2.a and
# headers under include/lzo, which is exactly what the repo's cmake/FindLZO.cmake expects.
ExternalProject_Add(dep_lzo2
    URL      https://www.oberhumer.com/opensource/lzo/download/lzo-2.10.tar.gz
    URL_HASH SHA256=c0f892943208266f9b6543b3ae308fab6284c5c90e627931446fb49b4221a072
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    CMAKE_ARGS
        ${_dep_common_args}
        -DENABLE_STATIC=ON
        -DENABLE_SHARED=OFF
    BUILD_ALWAYS OFF
)
