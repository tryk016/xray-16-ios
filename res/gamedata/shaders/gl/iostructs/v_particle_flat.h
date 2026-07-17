
#ifndef GL_ES
out gl_PerVertex { vec4 gl_Position; };
#endif

struct 		vv
{
	float4	P		; // POSITION;
	float2	tc		; // TEXCOORD0;
	float4	c		; // COLOR0;
};

struct 		v2p_particle
{
	float4 		color	; // COLOR0;
	v2p_flat	base;
};

layout(location = POSITION)		in float4	v_particle_P	; // POSITION;
layout(location = TEXCOORD0)	in float2	v_particle_tc	; // TEXCOORD0;
layout(location = COLOR)		in float4	v_particle_c	; // COLOR; 

VARYING(COLOR0) out float4	xrvary0; // COLOR0;
#if defined(USE_R2_STATIC_SUN) && !defined(USE_LM_HEMI)
VARYING(TEXCOORD0) out float4	xrvary8	; // TEXCOORD0;	// Texture coordinates,         w=sun_occlusion
#else
VARYING(TEXCOORD0) out float4	xrvary8	; // TEXCOORD0;	// Texture coordinates
#endif
VARYING(TEXCOORD1) out float4	xrvary9; // TEXCOORD1;	// position + hemi
VARYING(TEXCOORD2) out float4	xrvary10		; // TEXCOORD2;	// Eye-space normal        (for lighting)
#ifdef USE_TDETAIL
VARYING(TEXCOORD3) out float4	xrvary11; // TEXCOORD3;	// d-bump
#endif
#ifdef USE_LM_HEMI
VARYING(TEXCOORD4) out float4	xrvary12	; // TEXCOORD4;	// lm-hemi
#endif

v2p_particle _main ( vv I );

void main()
{
	vv		I;
	I.P		= v_particle_P;
	I.tc	= v_particle_tc;
	I.c 	= v_particle_c;

	v2p_particle O = _main (I);

	xrvary0 = O.color;
#if defined(USE_R2_STATIC_SUN) && !defined(USE_LM_HEMI)
	xrvary8 = O.base.tcdh;
#else
	xrvary8 = float4(O.base.tcdh, 0.0, 1.0);
#endif
	xrvary9 = O.base.position;
	xrvary10	= float4(O.base.N, 1.0);
#ifdef USE_TDETAIL
	xrvary11 = float4(O.base.tcdbump, 0.0, 1.0);
#endif
#ifdef USE_LM_HEMI
	xrvary12 = float4(O.base.lmh, 0.0, 1.0);
#endif
	gl_Position = O.base.hpos;
}
