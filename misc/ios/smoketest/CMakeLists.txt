# Standalone iOS smoke-test project.
#
# Validates the iOS cross-compile toolchain in CI WITHOUT pulling in the engine, so
# a failure here unambiguously points at the toolchain / SDK / CI rather than the
# engine port. Configure (from this directory) with, e.g.:
#
#   cmake -B build -G Xcode \
#     -DCMAKE_TOOLCHAIN_FILE=../../../cmake/toolchains/ios.toolchain.cmake \
#     -DPLATFORM=SIMULATORARM64 -DDEPLOYMENT_TARGET=16.4 -DENABLE_BITCODE=OFF
#   cmake --build build --config Release
#
# PLATFORM values used by the port: SIMULATORARM64 (Apple-silicon simulator) and
# OS64 (arm64 device). See cmake/toolchains/ios.toolchain.cmake for the full list.

cmake_minimum_required(VERSION 3.23)

if(NOT DEFINED DEPLOYMENT_TARGET)
    set(DEPLOYMENT_TARGET 16.4 CACHE STRING "Minimum supported iOS version")
endif()

project(OpenXRayIOSSmokeTest LANGUAGES CXX)

add_executable(ios_smoketest MACOSX_BUNDLE main.cpp)

set_target_properties(ios_smoketest PROPERTIES
    MACOSX_BUNDLE_GUI_IDENTIFIER   "io.github.openxray.iossmoketest"
    XCODE_ATTRIBUTE_PRODUCT_BUNDLE_IDENTIFIER "io.github.openxray.iossmoketest"
    MACOSX_BUNDLE_BUNDLE_NAME      "OpenXRay iOS Smoke Test"
    MACOSX_BUNDLE_BUNDLE_VERSION   "1.0"
    MACOSX_BUNDLE_SHORT_VERSION_STRING "1.0"
    # No signing needed to prove the pipeline; simulator never needs it and we build
    # unsigned device binaries in CI (on-device signing is a Phase 5 concern).
    XCODE_ATTRIBUTE_CODE_SIGNING_ALLOWED  "NO"
    XCODE_ATTRIBUTE_CODE_SIGNING_REQUIRED "NO"
)
