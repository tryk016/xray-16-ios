
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


VARYING(TEXCOORD0) out float4	xrvary8		; // TEXCOORD0;		// base
VARYING(TEXCOORD1) out float4	xrvary9		; // TEXCOORD1;		// lmap
VARYING(TEXCOORD2) out float4	xrvary10		; // TEXCOORD2;		// hemi
VARYING(TEXCOORD3) out float4	xrvary11		; // TEXCOORD3;		// env
VARYING(COLOR0) out float4	xrvary0		; // COLOR0;
VARYING(COLOR1) out float4	xrvary1		; // COLOR1;
VARYING(FOG) out float4	xrvary7		; // FOG;

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

	xrvary8	= float4(O.tc0, 0.0, 1.0);
	xrvary9	= float4(O.tc1, 0.0, 1.0);
	xrvary10	= float4(O.tc2, 0.0, 1.0);
	xrvary11	= float4(O.tc3, 1.0);
	xrvary0	= float4(O.c0, 1.0);
	xrvary1	= float4(O.c1, 1.0);
	xrvary7	= float4(O.fog, 0.0, 0.0, 1.0);
	gl_Position	= O.hpos;
}
