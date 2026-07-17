
out vec4 SV_Target;
#ifdef GBUFFER_OPTIMIZATION
#ifndef GL_ES
in vec4 gl_FragCoord;
#endif
#endif // GBUFFER_OPTIMIZATION

struct v2p
{
	float2 tc0	; // TEXCOORD0;
	float4 c	; // COLOR0;

//	Igor: for additional depth dest
#ifdef USE_SOFT_PARTICLES
	float4 tctexgen	; // TEXCOORD1;
#endif // USE_SOFT_PARTICLES
#ifdef GBUFFER_OPTIMIZATION
	float4 hpos	; // SV_Position;
#endif // USE_SOFT_PARTICLES
};

VARYING(TEXCOORD0) in float4	xrvary8	; // TEXCOORD0;
VARYING(COLOR0) in float4	xrvary0	; // COLOR0;
#ifdef	USE_SOFT_PARTICLES
VARYING(TEXCOORD1) in float4	xrvary9; // TEXCOORD1;
#endif	//	USE_SOFT_PARTICLES

float4 _main ( v2p I );

void main()
{
	v2p			I;
	I.tc0		= xrvary8.xy;
	I.c 		= xrvary0;
#ifdef USE_SOFT_PARTICLES
	I.tctexgen 	= xrvary9;
#endif // USE_SOFT_PARTICLES
#ifdef GBUFFER_OPTIMIZATION
	I.hpos		= gl_FragCoord;
#endif // GBUFFER_OPTIMIZATION
	SV_Target	= _main (I);
}
