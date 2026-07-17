
#ifndef GL_ES
out gl_PerVertex { vec4 gl_Position; };
#endif

struct v2p
{
	float4  hpos	; // SV_Position;
 	float2 	tc0	; // TEXCOORD0;		// base
  	float4	c0	; // COLOR0;		// color
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
VARYING(COLOR0) out float4	xrvary0		; // COLOR0;		// color

v2p _main ( v_static v );

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
	xrvary0	= O.c0;
	gl_Position	= O.hpos;
}
