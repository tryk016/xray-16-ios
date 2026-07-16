
#ifndef GL_ES
out gl_PerVertex { vec4 gl_Position; };
#endif

struct v2p
{
	float4  hpos	;	// SV_Position;
 	float2 	tc0	; 	// TEXCOORD0;		// base
 	float2 	tc1	; 	// TEXCOORD1;		// lmap
	float2	tc2	; 	// TEXCOORD2;		// hemi
	float3	tc3	; 	// TEXCOORD3;		// env
  	float3	c0	; 	// COLOR0;
	float3	c1	; 	// COLOR1;
	float   fog	; 	// FOG;
};

layout(location = POSITION)		in float4	v_static_P		; // POSITION;		// (float,float,float,1)
layout(location = NORMAL)		in float4	v_static_N		; // NORMAL;		// (nx,ny,nz,hemi occlusion)
layout(location = TANGENT)		in float4	v_static_T		; // TANGENT;		// tangent
layout(location = BINORMAL)		in float4	v_static_B		; // BINORMAL;		// binormal
layout(location = TEXCOORD0)		in float2	v_static_tc		; // TEXCOORD0;		// (u,v)
#ifdef USE_LM_HEMI
layout(location = TEXCOORD1)		in float2	v_static_lmh		; // TEXCOORD1;		// (lmu,lmv)
#endif


VARYING(TEXCOORD0) out float2	xrvary8		; // TEXCOORD0;		// base
VARYING(TEXCOORD1) out float2	xrvary9		; // TEXCOORD1;		// lmap
VARYING(TEXCOORD2) out float2	xrvary10		; // TEXCOORD2;		// hemi
VARYING(TEXCOORD3) out float3	xrvary11		; // TEXCOORD3;		// env
VARYING(COLOR0) out float3	xrvary0		; // COLOR0;
VARYING(COLOR1) out float3	xrvary1		; // COLOR1;
VARYING(FOG) out float	xrvary7		; // FOG;

v2p _main ( v_static I );

void main()
{
	v_static	I;
	I.P		= v_static_P;
	I.Nh		= v_static_N;
	I.T		= v_static_T;
	I.B		= v_static_B;
	I.tc		= v_static_tc;
#ifdef USE_LM_HEMI
	I.lmh		= v_static_lmh;
#endif

	v2p O 		= _main (I);

	xrvary8	= O.tc0;
	xrvary9	= O.tc1;
	xrvary10	= O.tc2;
	xrvary11	= O.tc3;
	xrvary0	= O.c0;
	xrvary1	= O.c1;
	xrvary7	= O.fog;
	gl_Position	= O.hpos;
}
