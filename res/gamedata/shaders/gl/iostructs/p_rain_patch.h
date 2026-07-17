
out vec4 SV_Target;
#ifdef MSAA_OPTIMIZATION
#ifndef GL_ES
in int gl_SampleID;
#endif
#endif
#ifdef GBUFFER_OPTIMIZATION
#ifndef GL_ES
in vec4 gl_FragCoord;
#endif
#endif

VARYING(TEXCOORD0) in float4	xrvary8	; // TEXCOORD0;
VARYING(TEXCOORD1) in float4	xrvary9	; // TEXCOORD1; 
VARYING(COLOR) in float4	xrvary0; // COLOR; 

#ifdef GBUFFER_OPTIMIZATION
#ifdef MSAA_OPTIMIZATION
float4 _main ( float2 tc, float2 tcJ, float4	Color, float4 pos2d, uint iSample  );
#else
float4 _main ( float2 tc, float2 tcJ, float4	Color, float4 pos2d );
#endif
#else
#ifdef MSAA_OPTIMIZATION
float4 _main ( float2 tc, float2 tcJ, uint iSample );
#else
float4 _main ( float2 tc, float2 tcJ );
#endif
#endif

void main()
{
#ifdef GBUFFER_OPTIMIZATION
#ifdef MSAA_OPTIMIZATION
	SV_Target	= _main ( xrvary8.xy, xrvary9.xy, xrvary0, gl_FragCoord, gl_SampleID  );
#else
	SV_Target	= _main ( xrvary8.xy, xrvary9.xy, xrvary0, gl_FragCoord );
#endif
#else
#ifdef MSAA_OPTIMIZATION
	SV_Target	= _main ( xrvary8.xy, xrvary9.xy, gl_SampleID );
#else
	SV_Target	= _main ( xrvary8.xy, xrvary9.xy );
#endif
#endif
}
