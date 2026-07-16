
out vec4 SV_Target;
#ifdef GBUFFER_OPTIMIZATION
#ifndef GL_ES
in vec4 gl_FragCoord;
#endif
#endif

struct v2p
{
	float2	tbase	; // TEXCOORD0;		// base
	float2	tnorm0	; // TEXCOORD1;		// nm0
	float2	tnorm1	; // TEXCOORD2;		// nm1
	float3	M1	; // TEXCOORD3;
	float3	M2	; // TEXCOORD4;
	float3	M3	; // TEXCOORD5;
	float3	v2point	; // TEXCOORD6;
#if defined(USE_SOFT_WATER) && defined(NEED_SOFT_WATER)
	float4	tctexgen; // TEXCOORD7;
#endif	// defined(USE_SOFT_WATER) && defined(NEED_SOFT_WATER)
#if SSR_QUALITY > 0
	float4	position_w;	// POSITION0;
#endif
	float4	c0	; // COLOR0;
	float	fog	; // FOG;
};

VARYING(TEXCOORD0) in float2	xrvary8		; // TEXCOORD0;
VARYING(TEXCOORD1) in float2	xrvary9	; // TEXCOORD1;
VARYING(TEXCOORD2) in float2	xrvary10	; // TEXCOORD2;
VARYING(TEXCOORD3) in float3	xrvary11		; // TEXCOORD3;
VARYING(TEXCOORD4) in float3	xrvary12		; // TEXCOORD4;
VARYING(TEXCOORD5) in float3	xrvary13		; // TEXCOORD5;
VARYING(TEXCOORD6) in float3	xrvary14	; // TEXCOORD6;
#if defined(USE_SOFT_WATER) && defined(NEED_SOFT_WATER)
VARYING(TEXCOORD7) in float4	xrvary15	; // TEXCOORD7;
#endif	// defined(USE_SOFT_WATER) && defined(NEED_SOFT_WATER)
#if SSR_QUALITY > 0
VARYING(POSITION0) in float4	xrvary3	; // POSITION0;
#endif
VARYING(COLOR0) in float4	xrvary0		; // COLOR0;
VARYING(FOG) in float	xrvary7		; // FOG;

#ifdef GBUFFER_OPTIMIZATION
float4 _main( v2p I, float4 pos2d );
#else
float4 _main( v2p I );
#endif

void main()
{
	v2p		I;

	I.tbase		= xrvary8;
	I.tnorm0	= xrvary9;
	I.tnorm1	= xrvary10;
	I.M1		= xrvary11;
	I.M2		= xrvary12;
	I.M3		= xrvary13;
	I.v2point	= xrvary14;
#if defined(USE_SOFT_WATER) && defined(NEED_SOFT_WATER)
	I.tctexgen	= xrvary15;
#endif	// defined(USE_SOFT_WATER) && defined(NEED_SOFT_WATER)
#if SSR_QUALITY > 0
    I.position_w = xrvary3;
#endif
	I.c0		= xrvary0;
	I.fog		= xrvary7;
#ifdef GBUFFER_OPTIMIZATION
	SV_Target	= _main ( I, gl_FragCoord );
#else
	SV_Target	= _main ( I );
#endif
}
