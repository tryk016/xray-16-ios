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
#     -DPLATFORM=OS64 -DDEPLOYMENT_TARGET=16.4 -DENABLE_BITCODE=OFF \
#     -DCMAKE_INSTALL_PREFIX=$PWD/build/ios-prefix-iphoneos
#   cmake --build build/ios-deps-iphoneos --parallel
#
# PLATFORM: OS64 (arm64 device) | SIMULATORARM64 (arm64 simulator).

cmake_minimum_required(VERSION 3.23)

project(OpenXRayIOSDeps LANGUAGES NONE)

include(ExternalProject)
find_package(Python3 REQUIRED COMPONENTS Interpreter)

# ---- Required / defaulted inputs ------------------------------------------------------
foreach(_req IOS_TOOLCHAIN_FILE PLATFORM)
    if(NOT DEFINED ${_req})
        message(FATAL_ERROR "Set -D${_req}=... (see the header of this file).")
    endif()
endforeach()

if(NOT DEFINED DEPLOYMENT_TARGET)
    set(DEPLOYMENT_TARGET 16.4)
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
set(_sdl2_scene_patch_script "${CMAKE_CURRENT_LIST_DIR}/../apply_sdl2_scene_patch.py")
set(_sdl2_scene_patch_manifest "${CMAKE_CURRENT_LIST_DIR}/../sdl2_scene_patch_manifest.json")
set(_sdl2_scene_patch "${CMAKE_CURRENT_LIST_DIR}/../patches/sdl2-2.32.10-uikit-scene.patch")
foreach(_sdl2_scene_patch_input IN ITEMS
        "${_sdl2_scene_patch_script}"
        "${_sdl2_scene_patch_manifest}"
        "${_sdl2_scene_patch}")
    if(NOT EXISTS "${_sdl2_scene_patch_input}")
        message(FATAL_ERROR "Required SDL2 UIKit scene patch input is missing: ${_sdl2_scene_patch_input}")
    endif()
endforeach()

# This generated command identity is a dependency of the ExternalProject step.
# Changing its command, source pin or patch inputs reruns the controlled patch
# before SDL's configure/build steps; an old ExternalProject stamp cannot hide it.
set(_sdl2_scene_patch_command_file
    "${CMAKE_CURRENT_BINARY_DIR}/openxray-sdl2-scene-patch-command.txt")
file(GENERATE OUTPUT "${_sdl2_scene_patch_command_file}" CONTENT
    "url=https://github.com/libsdl-org/SDL/releases/download/release-2.32.10/SDL2-2.32.10.tar.gz\nsha256=5f5993c530f084535c65a6879e9b26ad441169b3e25d789d83287040a9ca5165\nscript=${_sdl2_scene_patch_script}\nmanifest=${_sdl2_scene_patch_manifest}\npatch=${_sdl2_scene_patch}\n")

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

ExternalProject_Add_Step(dep_SDL2 openxray_uikit_scene_patch
    COMMAND "${Python3_EXECUTABLE}" "${_sdl2_scene_patch_script}"
        --source <SOURCE_DIR>
        --manifest "${_sdl2_scene_patch_manifest}"
        --patch "${_sdl2_scene_patch}"
        --url https://github.com/libsdl-org/SDL/releases/download/release-2.32.10/SDL2-2.32.10.tar.gz
        --url-sha256 5f5993c530f084535c65a6879e9b26ad441169b3e25d789d83287040a9ca5165
    DEPENDS
        "${CMAKE_CURRENT_LIST_FILE}"
        "${_sdl2_scene_patch_command_file}"
        "${_sdl2_scene_patch_script}"
        "${_sdl2_scene_patch_manifest}"
        "${_sdl2_scene_patch}"
    DEPENDEES update
    DEPENDERS configure
    COMMENT "Applying the checked SDL2 UIKit UIScene lifecycle backport"
)

# ---- OpenAL Soft ----------------------------------------------------------------------
# 3D audio. OpenAL Soft (not Apple's deprecated OpenAL.framework); modern CMake with a
# built-in iOS/CoreAudio backend. Static, with utilities/examples/dlopen disabled (iOS
# forbids dlopen, so backends must be linked in directly).
ExternalProject_Add(dep_openal
    URL      https://github.com/kcat/openal-soft/archive/refs/tags/1.25.2.tar.gz
    URL_HASH SHA256=fb27e5839aa11f0e5b9d33756965291fad5d6909ab928ea1f796f4a1a6877894
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    CMAKE_ARGS
        ${_dep_common_args}
        -DLIBTYPE=STATIC
        -DALSOFT_DLOPEN=OFF
        -DALSOFT_UTILS=OFF
        -DALSOFT_EXAMPLES=OFF
        -DALSOFT_TESTS=OFF
        -DALSOFT_EAX=OFF
        -DALSOFT_UPDATE_BUILD_VERSION=OFF
        -DALSOFT_INSTALL_CONFIG=OFF
        -DALSOFT_INSTALL_HRTF_DATA=OFF
        -DALSOFT_INSTALL_AMBDEC_PRESETS=OFF
        -DALSOFT_INSTALL_EXAMPLES=OFF
        -DALSOFT_INSTALL_UTILS=OFF
        # Xcode 26.5's clang adds -Wfunction-effects; openal-soft turns it into
        # -Werror=function-effects (even with ALSOFT_WERROR=OFF), which fails on the
        # CoreAudio callback. Pre-seed the compiler-flag probe to OFF so neither
        # -Wfunction-effects nor -Werror=function-effects is added.
        -DHAVE_WFUNCTION_EFFECTS=OFF
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
