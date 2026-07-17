
#ifndef GL_ES
out gl_PerVertex { vec4 gl_Position; };
#endif

layout(location = POSITION)		in float4	v_detail_pos	; // POSITION;                // (float,float,float,1)
layout(location = TEXCOORD0)	in float4	v_detail_misc	; // TEXCOORD0;        // (u(Q),v(Q),frac,matrix-id)

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

v2p_flat 	_main (v_detail v);

void main()
{
	v_detail	I;
	I.pos		= v_detail_pos;
	I.misc		= v_detail_misc;

	v2p_flat O	= _main (I);

#if defined(USE_R2_STATIC_SUN) && !defined(USE_LM_HEMI)
	xrvary8 = O.tcdh;
#else
	xrvary8 = float4(O.tcdh, 0.0, 1.0);
#endif
	xrvary9 = O.position;
	xrvary10	= float4(O.N, 1.0);
#ifdef USE_TDETAIL
	xrvary11 = float4(O.tcdbump, 0.0, 1.0);
#endif
#ifdef USE_LM_HEMI
	xrvary12 = float4(O.lmh, 0.0, 1.0);
#endif
	gl_Position = O.hpos;
}
