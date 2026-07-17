
out vec4 SV_Target;
#ifdef GBUFFER_OPTIMIZATION
#ifndef GL_ES
in vec4 gl_FragCoord;
#endif
#endif // GBUFFER_OPTIMIZATION
#ifdef USE_MSAA
out float gl_FragDepth;
#endif

struct c2_out
{
	float4	Color ; // SV_Target;
#ifdef USE_MSAA
	float	Depth ; // SV_Depth;
#endif
};

VARYING(TEXCOORD0) in float4 	xrvary8	; // TEXCOORD0;
VARYING(TEXCOORD1) in float4	xrvary9	; // TEXCOORD1;
VARYING(TEXCOORD2) in float4 	xrvary10	; // TEXCOORD2;
VARYING(TEXCOORD3) in float4	xrvary11	; // TEXCOORD3;
VARYING(TEXCOORD4) in float4 	xrvary12	; // TEXCOORD4;
VARYING(TEXCOORD5) in float4	xrvary13	; // TEXCOORD5;
VARYING(TEXCOORD6) in float4 	xrvary14	; // TEXCOORD6;

c2_out _main ( v2p_aa_AA I );

void main()
{
	v2p_aa_AA	I;
#ifdef GBUFFER_OPTIMIZATION
	I.HPos		= gl_FragCoord;
#endif // GBUFFER_OPTIMIZATION
	I.Tex0		= xrvary8.xy;
	I.Tex1		= xrvary9.xy;
	I.Tex2		= xrvary10.xy;
	I.Tex3		= xrvary11.xy;
	I.Tex4		= xrvary12.xy;
	I.Tex5		= xrvary13;
	I.Tex6		= xrvary14;

	c2_out O	= _main (I);

	SV_Target	= O.Color;
#ifdef USE_MSAA
	gl_FragDepth = O.Depth;
#endif
}
