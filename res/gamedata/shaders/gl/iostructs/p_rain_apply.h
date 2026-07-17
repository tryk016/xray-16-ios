
out vec4 SV_Target;
#ifdef MSAA_OPTIMIZATION
#ifndef GL_ES
in int gl_SampleID;
#endif
#endif

VARYING(TEXCOORD0) in float4	xrvary8	; // TEXCOORD0;
VARYING(TEXCOORD1) in float4	xrvary9	; // TEXCOORD1; 

#ifdef MSAA_OPTIMIZATION
float4 _main ( float2 tc, float2 tcJ, uint iSample );
#else
float4 _main ( float2 tc, float2 tcJ );
#endif

void main()
{
#ifdef MSAA_OPTIMIZATION
	SV_Target	= _main ( xrvary8.xy, xrvary9.xy, gl_SampleID );
#else
	SV_Target	= _main ( xrvary8.xy, xrvary9.xy );
#endif
}
