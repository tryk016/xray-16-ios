add_library(xrImGui STATIC)

set(IMGUI_DIR "${CMAKE_SOURCE_DIR}/Externals/imgui")

target_sources(xrImGui PRIVATE
    "${IMGUI_DIR}/imconfig.h"
    "${IMGUI_DIR}/imgui.cpp"
    "${IMGUI_DIR}/imgui.h"
    "${IMGUI_DIR}/imgui_demo.cpp"
    "${IMGUI_DIR}/imgui_draw.cpp"
    "${IMGUI_DIR}/imgui_tables.cpp"
    "${IMGUI_DIR}/imgui_widgets.cpp"
    "${IMGUI_DIR}/imgui_internal.h"
    "${IMGUI_DIR}/backends/imgui_impl_opengl3.cpp"
    "${IMGUI_DIR}/backends/imgui_impl_opengl3.h"
    "${IMGUI_DIR}/imstb_rectpack.h"
    "${IMGUI_DIR}/imstb_textedit.h"
    "${IMGUI_DIR}/imstb_truetype.h"
)

target_include_directories(xrImGui
    PUBLIC
    "${IMGUI_DIR}"
)

if (NOT CMAKE_SYSTEM_NAME STREQUAL "OpenBSD" AND NOT CMAKE_SYSTEM_NAME STREQUAL "NetBSD" AND NOT WIN32 AND NOT HAIKU)
    target_link_libraries(xrImGui
        PRIVATE
        $<$<NOT:$<PLATFORM_ID:OpenBSD>>:dl>
    )
endif()

target_compile_definitions(xrImGui
    PUBLIC
    IMGUI_DISABLE_OBSOLETE_KEYIO
    IMGUI_DISABLE_OBSOLETE_FUNCTIONS
    IMGUI_DEFINE_MATH_OPERATORS

    PRIVATE
    IMGUI_EXPORTS
)

if (XRAY_PLATFORM_IOS)
    # imgui_impl_opengl3.cpp is compiled unconditionally. On iOS there is no desktop
    # GL loader (gl3w/glad-desktop); this makes the backend include <OpenGLES/ES3/gl.h>
    # from the iOS SDK instead. Runtime renderer correctness is Phase 4.
    target_compile_definitions(xrImGui PRIVATE IMGUI_IMPL_OPENGL_ES3)
endif()

set_target_properties(xrImGui PROPERTIES
    PREFIX ""
    POSITION_INDEPENDENT_CODE ${BUILD_SHARED_LIBS}
)
