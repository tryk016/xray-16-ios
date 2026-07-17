
out vec4 SV_Target;
#ifdef GBUFFER_OPTIMIZATION
#ifndef GL_ES
in vec4 gl_FragCoord;
#endif
#endif

struct v2p
{
	float2  tbase	; // TEXCOORD0;
	float2  tdist0	; // TEXCOORD1;
	float2  tdist1	; // TEXCOORD2;
#if defined(USE_SOFT_WATER) && defined(NEED_SOFT_WATER)
	float4  tctexgen; // TEXCOORD3;
#endif	// defined(USE_SOFT_WATER) && defined(NEED_SOFT_WATER)
#ifdef GBUFFER_OPTIMIZATION
	float4  hpos	;	// SV_Position;
#endif
};

VARYING(TEXCOORD0) in float4	xrvary8		; // TEXCOORD0;
VARYING(TEXCOORD1) in float4	xrvary9		; // TEXCOORD1;
VARYING(TEXCOORD2) in float4	xrvary10		; // TEXCOORD2;		
#if defined(USE_SOFT_WATER) && defined(NEED_SOFT_WATER)
VARYING(TEXCOORD3) in float4	xrvary11		; // TEXCOORD3;
#endif	// defined(USE_SOFT_WATER) && defined(NEED_SOFT_WATER)

float4 _main ( v2p I );

void main()
{
	v2p		I;
	I.tbase		= xrvary8.xy;
	I.tdist0	= xrvary9.xy;
	I.tdist1	= xrvary10.xy;
#if defined(USE_SOFT_WATER) && defined(NEED_SOFT_WATER)
	I.tctexgen	= xrvary11;
#endif	// defined(USE_SOFT_WATER) && defined(NEED_SOFT_WATER)
#ifdef GBUFFER_OPTIMIZATION
	I.hpos	= gl_FragCoord;
#endif
	SV_Target	= _main (I);
}
