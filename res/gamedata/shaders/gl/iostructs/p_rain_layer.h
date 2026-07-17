
out vec4 SV_Target;
#ifdef GBUFFER_OPTIMIZATION
#ifndef GL_ES
in vec4 gl_FragCoord;
#endif
#endif

VARYING(TEXCOORD0) in float4	xrvary8	; // TEXCOORD0;
VARYING(TEXCOORD1) in float4	xrvary9	; // TEXCOORD1; 

#ifdef GBUFFER_OPTIMIZATION
float4 _main ( float2 tc, float2 tcJ, float4 pos2d );
#else
float4 _main ( float2 tc, float2 tcJ );
#endif

void main()
{
#ifdef GBUFFER_OPTIMIZATION
	SV_Target	= _main ( xrvary8.xy, xrvary9.xy, gl_FragCoord );
#else
	SV_Target	= _main ( xrvary8.xy, xrvary9.xy );
#endif
}
