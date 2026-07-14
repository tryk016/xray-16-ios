
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

VARYING(TEXCOORD0) in float2	p_rain_tc	; // TEXCOORD0;
VARYING(TEXCOORD1) in float2	p_rain_tcJ	; // TEXCOORD1; 
VARYING(COLOR) in float4	p_rain_Color; // COLOR; 

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
	SV_Target	= _main ( p_rain_tc, p_rain_tcJ, p_rain_Color, gl_FragCoord, gl_SampleID  );
#else
	SV_Target	= _main ( p_rain_tc, p_rain_tcJ, p_rain_Color, gl_FragCoord );
#endif
#else
#ifdef MSAA_OPTIMIZATION
	SV_Target	= _main ( p_rain_tc, p_rain_tcJ, gl_SampleID );
#else
	SV_Target	= _main ( p_rain_tc, p_rain_tcJ );
#endif
#endif
}
