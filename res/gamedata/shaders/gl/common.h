#ifndef        COMMON_H
#define        COMMON_H

// OpenGL ES has no implicit default precision (desktop GL does), so every shader
// must declare one for float/int and each sampler type BEFORE first use. glslang
// and the on-device ES driver both predefine GL_ES for a `#version NNN es` unit, so
// this block is desktop-safe (compiled out under desktop GL). See iOS port Plan 4.4.
#ifdef GL_ES
precision highp float;
precision highp int;
precision highp sampler2D;
precision highp sampler3D;
precision highp samplerCube;
precision highp sampler2DShadow;
#endif

// vs->fs varyings carry an explicit `layout(location=...)` so the desktop separable-
// program path can match them across stages. OpenGL ES 3.00 forbids location qualifiers
// on vertex outputs / fragment inputs (they match by name in a monolithic program), so
// VARYING() drops the qualifier under ES and keeps it on desktop. Vertex *attribute*
// inputs keep their raw layout(location=) — ES allows and needs those.
#ifdef GL_ES
#  define VARYING(loc)
#else
#  define VARYING(loc) layout(location = loc)
#endif

// Engine feature-quality options are only #defined when the feature is enabled; the
// shaders test them with `#if` (never `#ifdef`). Desktop GLSL treats an undefined macro
// in a `#if` as 0, but GLSL ES rejects it ("undefined macro in expression not allowed"),
// so give them explicit 0 defaults. Behaviour is unchanged on desktop (undefined == 0).
#ifndef SUN_QUALITY
#  define SUN_QUALITY 0
#endif
#ifndef SSR_QUALITY
#  define SSR_QUALITY 0
#endif
#ifndef MSAA_SAMPLES
#  define MSAA_SAMPLES 0
#endif

#include "shared\common.h"

#include "common_defines.h"
#include "common_policies.h"
#include "common_iostructs.h"
#include "common_samplers.h"
#include "common_cbuffers.h"
#include "common_functions.h"

// #define USE_SUPER_SPECULAR

#ifdef        USE_R2_STATIC_SUN
#  define xmaterial float(1.0/4.0)
#else
#  define xmaterial float(L_material.w)
#endif

#endif
