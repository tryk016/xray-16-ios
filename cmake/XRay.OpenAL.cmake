include_guard(GLOBAL)

if (NOT XRAY_PLATFORM_IOS)
    message(FATAL_ERROR "XRay.OpenAL is reserved for XRAY_PLATFORM_IOS")
endif()

if (TARGET OpenAL::OpenAL)
    message(FATAL_ERROR "iOS OpenAL provider must be project-owned, but OpenAL::OpenAL already exists")
endif()

# ios-cmake appends its SDK (and, due to an upstream list() typo, literal
# CACHE/INTERNAL entries) to the normal-scope root-path variable after CMake
# has read the command-line cache entry. The cache value is the explicit build
# input and therefore the only reliable value to validate here.
function(_xray_openal_read_single_path variable output)
    get_property(_xray_openal_cached CACHE "${variable}" PROPERTY TYPE SET)
    if (_xray_openal_cached)
        get_property(_xray_openal_value CACHE "${variable}" PROPERTY VALUE)
    else()
        set(_xray_openal_value "${${variable}}")
    endif()

    list(LENGTH _xray_openal_value _xray_openal_count)
    if (NOT _xray_openal_count EQUAL 1)
        message(FATAL_ERROR "${variable} must contain exactly one path")
    endif()

    list(GET _xray_openal_value 0 _xray_openal_path)
    if ("${_xray_openal_path}" STREQUAL "")
        message(FATAL_ERROR "${variable} must contain one nonempty path")
    endif()

    set(${output} "${_xray_openal_path}" PARENT_SCOPE)
endfunction()

function(_xray_openal_require_contained path root description)
    file(REAL_PATH "${path}" _xray_openal_real_path)
    file(RELATIVE_PATH _xray_openal_relative_path "${root}" "${_xray_openal_real_path}")
    if (_xray_openal_relative_path STREQUAL "" OR
        _xray_openal_relative_path STREQUAL "." OR
        _xray_openal_relative_path STREQUAL ".." OR
        _xray_openal_relative_path MATCHES "^\\.\\./" OR
        IS_ABSOLUTE "${_xray_openal_relative_path}")
        message(FATAL_ERROR
            "${description} must resolve below ${root}, got ${_xray_openal_real_path}")
    endif()
endfunction()

function(_xray_openal_require_directory path root description)
    if (NOT IS_DIRECTORY "${path}")
        message(FATAL_ERROR "${description} must be an existing directory: ${path}")
    endif()
    _xray_openal_require_contained("${path}" "${root}" "${description}")
endfunction()

function(_xray_openal_require_regular_file path root description)
    if (NOT EXISTS "${path}" OR IS_DIRECTORY "${path}")
        message(FATAL_ERROR "${description} must be an existing regular file: ${path}")
    endif()

    # CMake 3.23 has no IS_REGULAR predicate. iOS configuration runs on macOS,
    # where /bin/test follows symlinks and accepts only regular files.
    execute_process(
        COMMAND /bin/test -f "${path}"
        RESULT_VARIABLE _xray_openal_regular_result
    )
    if (NOT _xray_openal_regular_result EQUAL 0)
        message(FATAL_ERROR "${description} must be a regular file: ${path}")
    endif()
    _xray_openal_require_contained("${path}" "${root}" "${description}")
endfunction()

function(_xray_openal_find_sdk_framework framework_root framework_name output)
    set(_xray_openal_expected "${framework_root}/${framework_name}.framework")
    unset(_xray_openal_framework)
    unset(_xray_openal_framework CACHE)
    find_library(_xray_openal_framework
        NAMES "${framework_name}"
        PATHS "${framework_root}"
        NO_DEFAULT_PATH
        NO_CMAKE_FIND_ROOT_PATH
    )
    if (NOT "${_xray_openal_framework}" STREQUAL "${_xray_openal_expected}")
        message(FATAL_ERROR
            "${framework_name}.framework must be found only at ${_xray_openal_expected}, got ${_xray_openal_framework}")
    endif()
    if (NOT IS_DIRECTORY "${_xray_openal_framework}")
        message(FATAL_ERROR "${framework_name}.framework is not a framework directory")
    endif()
    _xray_openal_require_contained(
        "${_xray_openal_framework}" "${framework_root}" "${framework_name}.framework")
    set(${output} "${_xray_openal_framework}" PARENT_SCOPE)
endfunction()

_xray_openal_read_single_path(CMAKE_PREFIX_PATH _xray_openal_prefix_input)
_xray_openal_read_single_path(CMAKE_FIND_ROOT_PATH _xray_openal_root_input)

if (NOT IS_DIRECTORY "${_xray_openal_prefix_input}")
    message(FATAL_ERROR "CMAKE_PREFIX_PATH must name an existing directory: ${_xray_openal_prefix_input}")
endif()
if (NOT IS_DIRECTORY "${_xray_openal_root_input}")
    message(FATAL_ERROR "CMAKE_FIND_ROOT_PATH must name an existing directory: ${_xray_openal_root_input}")
endif()
file(REAL_PATH "${_xray_openal_prefix_input}" _xray_openal_prefix)
file(REAL_PATH "${_xray_openal_root_input}" _xray_openal_root)
if (NOT "${_xray_openal_prefix}" STREQUAL "${_xray_openal_root}")
    message(FATAL_ERROR
        "CMAKE_PREFIX_PATH and CMAKE_FIND_ROOT_PATH must resolve to the same iOS dependency prefix")
endif()

set(_xray_openal_library "${_xray_openal_prefix}/lib/libopenal.a")
set(_xray_openal_include_root "${_xray_openal_prefix}/include")
set(_xray_openal_include_al "${_xray_openal_include_root}/AL")
_xray_openal_require_regular_file(
    "${_xray_openal_library}" "${_xray_openal_prefix}" "OpenAL archive")
_xray_openal_require_directory(
    "${_xray_openal_include_root}" "${_xray_openal_prefix}" "OpenAL include root")
_xray_openal_require_directory(
    "${_xray_openal_include_al}" "${_xray_openal_prefix}" "OpenAL AL include directory")
foreach(_xray_openal_header IN ITEMS al.h alc.h alext.h)
    _xray_openal_require_regular_file(
        "${_xray_openal_include_al}/${_xray_openal_header}"
        "${_xray_openal_prefix}"
        "OpenAL header ${_xray_openal_header}")
endforeach()

if ("${CMAKE_OSX_SYSROOT}" STREQUAL "" OR NOT IS_DIRECTORY "${CMAKE_OSX_SYSROOT}")
    message(FATAL_ERROR "CMAKE_OSX_SYSROOT must name an existing iOS SDK directory")
endif()
file(REAL_PATH "${CMAKE_OSX_SYSROOT}" _xray_openal_sysroot)
set(_xray_openal_framework_root "${_xray_openal_sysroot}/System/Library/Frameworks")
if (NOT IS_DIRECTORY "${_xray_openal_framework_root}")
    message(FATAL_ERROR "iOS SDK frameworks directory is missing: ${_xray_openal_framework_root}")
endif()

_xray_openal_find_sdk_framework(
    "${_xray_openal_framework_root}" AudioToolbox _xray_openal_audiotoolbox)
_xray_openal_find_sdk_framework(
    "${_xray_openal_framework_root}" CoreFoundation _xray_openal_corefoundation)
_xray_openal_find_sdk_framework(
    "${_xray_openal_framework_root}" CoreAudio _xray_openal_coreaudio)

set(OPENAL_LIBRARY "${_xray_openal_library}" CACHE FILEPATH "iOS OpenAL Soft static archive" FORCE)
set(OPENAL_INCLUDE_DIR "${_xray_openal_include_root}" CACHE PATH "iOS OpenAL Soft include root" FORCE)
set(XRAY_OPENAL_PROVIDER "OpenALSoft-1.25.2-static" CACHE STRING "Selected iOS OpenAL provider" FORCE)
set(XRAY_OPENAL_PREFIX "${_xray_openal_prefix}" CACHE PATH "Validated iOS OpenAL prefix" FORCE)
set(XRAY_OPENAL_LIBRARY "${_xray_openal_library}" CACHE FILEPATH "Validated iOS OpenAL archive" FORCE)
set(XRAY_OPENAL_INCLUDE_ROOT "${_xray_openal_include_root}" CACHE PATH "Validated iOS OpenAL include root" FORCE)

find_package(Threads REQUIRED)
add_library(OpenAL::OpenAL STATIC IMPORTED GLOBAL)
set_target_properties(OpenAL::OpenAL PROPERTIES
    IMPORTED_LOCATION "${_xray_openal_library}"
    INTERFACE_INCLUDE_DIRECTORIES "${_xray_openal_include_root};${_xray_openal_include_al}"
    INTERFACE_COMPILE_DEFINITIONS "AL_LIBTYPE_STATIC"
    INTERFACE_LINK_LIBRARIES
        "Threads::Threads;m;${_xray_openal_audiotoolbox};${_xray_openal_corefoundation};${_xray_openal_coreaudio}"
)
