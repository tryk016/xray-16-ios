
out vec4 SV_Target;
#ifdef GBUFFER_OPTIMIZATION
#ifndef GL_ES
in vec4 gl_FragCoord;
#endif
#endif // GBUFFER_OPTIMIZATION

VARYING(TEXCOORD0) in float2 	xrvary8	; // TEXCOORD0;
VARYING(TEXCOORD1) in float2	xrvary9	; // TEXCOORD1;
VARYING(TEXCOORD2) in float2 	xrvary10	; // TEXCOORD2;
VARYING(TEXCOORD3) in float2	xrvary11	; // TEXCOORD3;
VARYING(TEXCOORD4) in float2 	xrvary12	; // TEXCOORD4;
VARYING(TEXCOORD5) in float4	xrvary13	; // TEXCOORD5;
VARYING(TEXCOORD6) in float4 	xrvary14	; // TEXCOORD6;

#ifdef GBUFFER_OPTIMIZATION
float4 _main ( v_aa_AA I, float4 pos2d );
#else // GBUFFER_OPTIMIZATION
float4 _main ( v_aa_AA I );
#endif // GBUFFER_OPTIMIZATION

void main()
{
	v_aa_AA	I;
#ifdef GBUFFER_OPTIMIZATION
	I.P			= gl_FragCoord;
#endif // GBUFFER_OPTIMIZATION
	I.Tex0		= xrvary8;
	I.Tex1		= xrvary9;
	I.Tex2		= xrvary10;
	I.Tex3		= xrvary11;
	I.Tex4		= xrvary12;
	I.Tex5		= xrvary13;
	I.Tex6		= xrvary14;

#ifdef GBUFFER_OPTIMIZATION
	SV_Target	= _main ( I, gl_FragCoord );
#else // GBUFFER_OPTIMIZATION
	SV_Target	= _main ( I );
#endif // GBUFFER_OPTIMIZATION
}
