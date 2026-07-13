# Standalone driver that builds ONLY the xrLuaJIT static library for iOS, to verify the
# LuaJIT cross-compile (native host codegen tools generating the VM, iphoneos target lib)
# in isolation before the full engine link. Configure with the leetal iOS toolchain:
#
#   cmake -S cmake/ios/luajit-check -B build \
#     -DCMAKE_TOOLCHAIN_FILE=<repo>/cmake/toolchains/ios.toolchain.cmake \
#     -DPLATFORM=OS64 -DDEPLOYMENT_TARGET=15.0 -DENABLE_BITCODE=OFF
#   cmake --build build --parallel
#
# Requires the Externals/LuaJIT submodule to be checked out.

cmake_minimum_required(VERSION 3.13)

project(luajit_ios_check C CXX ASM)

include(GNUInstallDirs)

get_filename_component(_repo_root "${CMAKE_CURRENT_SOURCE_DIR}/../../.." ABSOLUTE)

# Point LuaJIT-proj at the in-repo submodule sources and force a single static lib
# (the engine sets BUILD_SHARED_LIBS=OFF on iOS too).
set(LUAJIT_DIR "${_repo_root}/Externals/LuaJIT/src" CACHE PATH "" FORCE)
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)

# iOS runs LuaJIT in interpreter mode (lj_arch.h already forces LJ_HASJIT=0 on iOS; the
# JIT can't allocate RWX without a SideStore/debug entitlement anyway). Disable the JIT
# explicitly so the host codegen tools and the target sources agree — otherwise buildvm
# emits jit.util.trace* libdefs that interpreter-mode lib_jit.c does not define.
set(LUAJIT_DISABLE_JIT ON CACHE BOOL "" FORCE)

add_subdirectory("${_repo_root}/Externals/LuaJIT-proj" luajit-build)
