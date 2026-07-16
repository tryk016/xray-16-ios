
#ifndef GL_ES
out gl_PerVertex { vec4 gl_Position; };
#endif

#if defined(USE_R2_STATIC_SUN) && !defined(USE_LM_HEMI)
#define	v_in	v_static_color	
#else
#define	v_in	v_static
#endif

layout(location = NORMAL)		in float4	v_static_Nh		; // NORMAL;	// (nx,ny,nz,hemi occlusion)
layout(location = TANGENT)		in float4	v_static_T		; // TANGENT;	// tangent
layout(location = BINORMAL)		in float4	v_static_B		; // BINORMAL;	// binormal
layout(location = TEXCOORD0)	in float2	v_static_tc		; // TEXCOORD0;	// (u,v)
#ifdef	USE_LM_HEMI
layout(location = TEXCOORD1)	in float2	v_static_lmh	; // TEXCOORD1;	// (lmu,lmv)
#endif
#if defined(USE_R2_STATIC_SUN) && !defined(USE_LM_HEMI)
layout(location = COLOR0)		in float4	v_static_C	; // COLOR0;	// (r,g,b,dir-occlusion)	//	Swizzle before use!!!
#endif
layout(location = POSITION)		in float4	v_static_P		; // POSITION;	// (float,float,float,1)

#if defined(USE_R2_STATIC_SUN) && !defined(USE_LM_HEMI)
VARYING(TEXCOORD0) out float4	xrvary8	; // TEXCOORD0;	// Texture coordinates,         w=sun_occlusion
#else
VARYING(TEXCOORD0) out float2	xrvary8	; // TEXCOORD0;	// Texture coordinates
#endif
VARYING(TEXCOORD1) out float4	xrvary9; // TEXCOORD1;	// position + hemi
VARYING(TEXCOORD2) out float3	xrvary10		; // TEXCOORD2;	// Eye-space normal        (for lighting)
#ifdef USE_TDETAIL
VARYING(TEXCOORD3) out float2	xrvary11; // TEXCOORD3;	// d-bump
#endif
#ifdef USE_LM_HEMI
VARYING(TEXCOORD4) out float2	xrvary12	; // TEXCOORD4;	// lm-hemi
#endif

v2p_flat _main( v_in I );

void main()
{
	v_in		I;
	I.Nh		= v_static_Nh;
	I.T			= v_static_T;
	I.B			= v_static_B;
	I.tc		= v_static_tc;
#ifdef	USE_LM_HEMI
	I.lmh		= v_static_lmh;
#endif
#if defined(USE_R2_STATIC_SUN) && !defined(USE_LM_HEMI)
	I.color		= v_static_C;
#endif
	I.P			= v_static_P;

	v2p_flat O	= _main (I);

	xrvary8 = O.tcdh;
	xrvary9 = O.position;
	xrvary10	= O.N;
#ifdef USE_TDETAIL
	xrvary11 = O.tcdbump;
#endif
#ifdef USE_LM_HEMI
	xrvary12 = O.lmh;
#endif
	gl_Position = O.hpos;
}
