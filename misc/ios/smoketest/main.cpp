// OpenXRay iOS pipeline smoke test.
//
// Purpose: prove the macOS-hosted cross-compile pipeline (leetal ios.toolchain.cmake
// + iphoneos/iphonesimulator SDK on a GitHub Actions macOS runner) produces a valid
// iOS binary, driven entirely from files authored on a Windows dev machine.
//
// This target intentionally has ZERO engine dependencies. A red build here means the
// toolchain / SDK / CI wiring is broken — nothing about the engine itself. The engine
// sources are brought in starting from Phase 2.

#include <TargetConditionals.h>
#include <cstdio>

#if !defined(__APPLE__)
#   error "Not building for an Apple platform - toolchain misconfigured."
#endif
#if !TARGET_OS_IPHONE
#   error "TARGET_OS_IPHONE is not set - expected an iOS (device or simulator) build."
#endif
#if !(defined(__aarch64__) || defined(__arm64__))
#   error "Expected an arm64 iOS build."
#endif

int main(int /*argc*/, char** /*argv*/)
{
    std::printf("OpenXRay iOS smoke test OK\n");
#if TARGET_OS_SIMULATOR
    std::printf("  target: iOS Simulator (arm64)\n");
#else
    std::printf("  target: iOS device (arm64)\n");
#endif
    return 0;
}
