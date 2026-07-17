
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
VARYING(COLOR) in float4	xrvary0	; // COLOR; 

#ifdef MSAA_OPTIMIZATION
#ifdef GBUFFER_OPTIMIZATION
float4 _main ( p_TL I, float4 pos2d, uint iSample );
#else
float4 _main ( p_TL I, uint iSample );
#endif
#else
#ifdef GBUFFER_OPTIMIZATION
float4 _main ( p_TL I, float4 pos2d );
#else
float4 _main ( p_TL I );
#endif
#endif

void main()
{
	p_TL		I;
	I.Tex0		= xrvary8.xy;
	I.Color 	= xrvary0;

#ifdef MSAA_OPTIMIZATION
#ifdef GBUFFER_OPTIMIZATION
	SV_Target	= _main ( I, gl_FragCoord, gl_SampleID );
#else
	SV_Target	= _main ( I, gl_SampleID );
#endif
#else
#ifdef GBUFFER_OPTIMIZATION
	SV_Target	= _main ( I, gl_FragCoord );
#else
	SV_Target	= _main ( I );
#endif
#endif
}
