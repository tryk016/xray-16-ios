include_guard()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# iOS: one self-contained static binary, interpreter-mode LuaJIT.
# CMAKE_SYSTEM_NAME is defined here (this file is included after project(), unlike
# PreProjectInit which runs before it), and before add_subdirectory(Externals)/src,
# so XRAY_PLATFORM_IOS propagates to every engine target.
if (CMAKE_SYSTEM_NAME STREQUAL "iOS")
    set(XRAY_PLATFORM_IOS ON)
    # iOS cannot dlopen dylibs from arbitrary paths -> everything links into one binary.
    set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
    # Interpreter-mode LuaJIT: without this the host buildvm emits jit.util.trace*
    # libdefs that interpreter-mode lib_jit.c does not define (undeclared identifier
    # lj_cf_jit_util_trace*). iOS forbids RWX pages so the JIT can't run anyway.
    set(LUAJIT_DISABLE_JIT ON CACHE BOOL "" FORCE)
endif()

# Output all libraries and executables to one folder. FastDevice uses the
# Release configuration for identical -O3/NDEBUG semantics, but a separate
# destination prevents it from overwriting the symbol-complete Release app.
if (XRAY_PLATFORM_IOS AND XRAY_IOS_FAST_DEVICE)
    # Keep Release optimization semantics without letting a multi-config
    # generator append a second /Release directory.
    set(XRAY_COMPILE_OUTPUT_FOLDER "${CMAKE_SOURCE_DIR}/bin/${CMAKE_SYSTEM_PROCESSOR}/FastDevice$<0:>")
else()
    set(XRAY_COMPILE_OUTPUT_FOLDER "${CMAKE_SOURCE_DIR}/bin/${CMAKE_SYSTEM_PROCESSOR}/$<CONFIG>")
endif()
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "${XRAY_COMPILE_OUTPUT_FOLDER}")
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY "${XRAY_COMPILE_OUTPUT_FOLDER}")
set(CMAKE_PDB_OUTPUT_DIRECTORY "${XRAY_COMPILE_OUTPUT_FOLDER}")
set(CMAKE_COMPILE_PDB_OUTPUT_DIRECTORY "${XRAY_COMPILE_OUTPUT_FOLDER}")

add_compile_definitions(
    # _DEBUG, DEBUG, MIXED, NDEBUG defines
    $<$<CONFIG:Debug>:_DEBUG>
    $<$<CONFIG:Debug,Mixed>:DEBUG>
    $<$<CONFIG:Mixed>:MIXED>
    $<$<CONFIG:Release,ReleaseMasterGold>:NDEBUG>
    # Tracy profiler
    $<$<BOOL:${XRAY_ENABLE_TRACY}>:TRACY_ENABLE>
    $<$<BOOL:${XRAY_ENABLE_TRACY}>:TRACY_NO_FRAME_IMAGE>
    # Luabind
    $<$<CONFIG:Release,ReleaseMasterGold>:LUABIND_NO_EXCEPTIONS>
    $<$<CONFIG:Release,ReleaseMasterGold>:LUABIND_NO_ERROR_CHECKING>
)

# Link-time optimization. The iteration tree skips the probe and IPO
# deliberately; the release tree retains the existing behavior.
set(LTO_IS_SUPPORTED OFF)
if (NOT XRAY_IOS_FAST_DEVICE)
    include(CheckIPOSupported)
    check_ipo_supported(RESULT LTO_IS_SUPPORTED)
    if (LTO_IS_SUPPORTED)
        set(CMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE ON)
        set(CMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASEMASTERGOLD ON)
    endif()
endif()

# Main compiler settings
if (CMAKE_CXX_COMPILER_ID STREQUAL "MSVC")
    include(XRay.Compiler.MSVC)
elseif (CMAKE_CXX_COMPILER_ID MATCHES "GNU|LCC|Clang")
    include(XRay.Compiler.GNULike)
else()
    message(FATAL_ERROR "Unsupported or unknown compiler.")
endif()

# https://gitlab.kitware.com/cmake/cmake/-/issues/25650
if (CMAKE_VERSION VERSION_EQUAL "3.28.2" AND CMAKE_UNITY_BUILD)
    message(WARNING
        "In CMake 3.28.2, precompiled headers are broken when Unity build is enabled. \
        We have to disable Unity build. Please, update to CMake 3.28.3 or downgrade to 3.28.1."
    )
    set(CMAKE_UNITY_BUILD OFF)
endif()

query_git_info(XRAY_GIT_SHA XRAY_GIT_BRANCH)

message(VERBOSE "CMAKE_UNITY_BUILD:     ${CMAKE_UNITY_BUILD}")
message(STATUS  "CMAKE_PROJECT_VERSION: ${CMAKE_PROJECT_VERSION}")
message(STATUS  "XRAY_GIT_SHA:          ${XRAY_GIT_SHA}")
message(STATUS  "XRAY_GIT_BRANCH:       ${XRAY_GIT_BRANCH}")

message(STATUS "BUILD_SHARED_LIBS:     ${BUILD_SHARED_LIBS}")
message(STATUS "LTO_IS_SUPPORTED:      ${LTO_IS_SUPPORTED}")

message(DEBUG)
message(DEBUG "C++ Flags:")
message(DEBUG "           Global: ${CMAKE_CXX_FLAGS}")
message(DEBUG "            Debug: ${CMAKE_CXX_FLAGS_DEBUG}")
message(DEBUG "            Mixed: ${CMAKE_CXX_FLAGS_MIXED}")
message(DEBUG "          Release: ${CMAKE_CXX_FLAGS_RELEASE}")
message(DEBUG "ReleaseMasterGold: ${CMAKE_CXX_FLAGS_RELEASEMASTERGOLD}")

message(DEBUG)
message(DEBUG "C Flags:")
message(DEBUG "           Global: ${CMAKE_C_FLAGS}")
message(DEBUG "            Debug: ${CMAKE_C_FLAGS_DEBUG}")
message(DEBUG "            Mixed: ${CMAKE_C_FLAGS_MIXED}")
message(DEBUG "          Release: ${CMAKE_C_FLAGS_RELEASE}")
message(DEBUG "ReleaseMasterGold: ${CMAKE_C_FLAGS_RELEASEMASTERGOLD}")
message(DEBUG)

unset(LTO_IS_SUPPORTED)
