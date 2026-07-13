# Minimal CMake build for libtheora 1.1.1, injected into the theora source tree by the
# iOS deps superbuild (cmake/ios/deps) via ExternalProject PATCH_COMMAND.
#
# Upstream libtheora ships only an autotools build, which does not cross-compile cleanly
# for iOS on a modern clang toolchain. This mirrors the portable (no-asm) source lists
# from lib/Makefile.am and builds static libs — the same approach vcpkg's theora port
# takes. OC_X86_ASM is left undefined so the pure-C fallbacks are used; theora 1.1.1 has
# no config.h and needs only the ogg headers at compile time.

cmake_minimum_required(VERSION 3.10)

project(theora C)

# ogg headers were installed into the shared prefix by the ogg sub-build (DEPENDS).
include_directories("${CMAKE_INSTALL_PREFIX}/include")
include_directories("${CMAKE_CURRENT_SOURCE_DIR}/include")
include_directories("${CMAKE_CURRENT_SOURCE_DIR}/lib")

# Portable source lists, verbatim from lib/Makefile.am (no x86/arm asm variants).
set(_decoder_sources
    lib/apiwrapper.c lib/bitpack.c lib/decapiwrapper.c lib/decinfo.c lib/decode.c
    lib/dequant.c lib/fragment.c lib/huffdec.c lib/idct.c lib/info.c lib/internal.c
    lib/quant.c lib/state.c)

set(_encoder_shared_sources
    lib/apiwrapper.c lib/fragment.c lib/idct.c lib/internal.c lib/state.c lib/quant.c)

set(_encoder_uniq_sources
    lib/analyze.c lib/fdct.c lib/encfrag.c lib/encapiwrapper.c lib/encinfo.c
    lib/encode.c lib/enquant.c lib/huffenc.c lib/mathops.c lib/mcenc.c lib/rate.c
    lib/tokenize.c)

# libtheora = the combined compat lib the engine links via Theora::Theora.
add_library(theora    STATIC ${_decoder_sources} ${_encoder_uniq_sources})
# Split libs too, so the repo's cmake/FindTheora.cmake resolves the dec/enc components
# (the engine only links Theora::Theora, so these are never linked together).
add_library(theoradec STATIC ${_decoder_sources})
add_library(theoraenc STATIC ${_encoder_shared_sources} ${_encoder_uniq_sources})

install(TARGETS theora theoradec theoraenc ARCHIVE DESTINATION lib)
install(DIRECTORY include/theora DESTINATION include)
