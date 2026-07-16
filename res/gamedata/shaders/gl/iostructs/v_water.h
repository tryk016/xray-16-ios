
#ifndef GL_ES
out gl_PerVertex { vec4 gl_Position; };
#endif

struct v_vert
{
	float4 	P	;	// POSITION;		// (float,float,float,1)
	float4	N	;	// NORMAL;		// (nx,ny,nz,hemi occlusion)
	float4 	T	;	// TANGENT;
	float4 	B	;	// BINORMAL;
	float4	color	;	// COLOR0;		// (r,g,b,dir-occlusion)
	float2 	uv	;	// TEXCOORD0;		// (u0,v0)
};
struct v2p
{
	float4	hpos	;	// SV_Position;
	float2	tbase	;	// TEXCOORD0;		// base
	float2	tnorm0	;	// TEXCOORD1;		// nm0
	float2	tnorm1	;	// TEXCOORD2;		// nm1
	float3	M1	;	// TEXCOORD3;
	float3	M2	;	// TEXCOORD4;
	float3	M3	;	// TEXCOORD5;
	float3	v2point	;	// TEXCOORD6;
#if defined(USE_SOFT_WATER) && defined(NEED_SOFT_WATER)
	float4	tctexgen;	// TEXCOORD7;
#endif	// defined(USE_SOFT_WATER) && defined(NEED_SOFT_WATER)
#if SSR_QUALITY > 0
	float4	position_w;	// POSITION0;
#endif
	float4	c0	;	// COLOR0;
	float	fog	;	// FOG;
};

layout(location = POSITION)		in float4	v_vert_P		; // POSITION;		// (float,float,float,1)
layout(location = NORMAL)		in float4	v_vert_N		; // NORMAL;		// (nx,ny,nz,hemi occlusion)
layout(location = TANGENT)		in float4	v_vert_T		; // TANGENT;		// tangent
layout(location = BINORMAL)		in float4	v_vert_B		; // BINORMAL;		// binormal
layout(location = COLOR0)		in float4	v_vert_color		; // COLOR0;		// (r,g,b,dir-occlusion)
layout(location = TEXCOORD0)		in float2	v_vert_uv		; // TEXCOORD0;		// (u0,v0)

VARYING(TEXCOORD0) out float2	xrvary8		; // TEXCOORD0;
VARYING(TEXCOORD1) out float2	xrvary9		; // TEXCOORD1;
VARYING(TEXCOORD2) out float2	xrvary10		; // TEXCOORD2;
VARYING(TEXCOORD3) out float3	xrvary11		; // TEXCOORD3;
VARYING(TEXCOORD4) out float3	xrvary12		; // TEXCOORD4;
VARYING(TEXCOORD5) out float3	xrvary13		; // TEXCOORD5;
VARYING(TEXCOORD6) out float3	xrvary14	; // TEXCOORD6;
#if defined(USE_SOFT_WATER) && defined(NEED_SOFT_WATER)
VARYING(TEXCOORD7) out float4	xrvary15	; // TEXCOORD7;
#endif	// defined(USE_SOFT_WATER) && defined(NEED_SOFT_WATER)
#if SSR_QUALITY > 0
VARYING(POSITION0) out float4	xrvary3	; // POSITION0;
#endif
VARYING(COLOR0) out float4	xrvary0		; // COLOR0;
VARYING(FOG) out float	xrvary7		; // FOG;

v2p _main (v_vert v);

void main()
{
	v_vert		I;
	I.P		= v_vert_P;
	I.N		= v_vert_N;
	I.T		= v_vert_T;
	I.B		= v_vert_B;
	I.color		= v_vert_color;
	I.uv		= v_vert_uv;

	v2p O 		= _main (I);

	xrvary8	= O.tbase;
	xrvary9	= O.tnorm0;
	xrvary10	= O.tnorm1;
	xrvary11	= O.M1;
	xrvary12	= O.M2;
	xrvary13	= O.M3;
	xrvary14= O.v2point;
#if defined(USE_SOFT_WATER) && defined(NEED_SOFT_WATER)
	xrvary15 = O.tctexgen;
#endif	// defined(USE_SOFT_WATER) && defined(NEED_SOFT_WATER)
	xrvary0	= O.c0;
	xrvary7	= O.fog;
#if SSR_QUALITY > 0
	xrvary3	=  O.position_w;
#endif
	gl_Position	= O.hpos;
}
