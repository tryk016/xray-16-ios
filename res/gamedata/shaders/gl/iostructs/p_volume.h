
out vec4 SV_Target;
#ifdef GBUFFER_OPTIMIZATION
#ifndef GL_ES
in vec4 gl_FragCoord;
#endif
#endif
#ifdef MSAA_OPTIMIZATION
#ifndef GL_ES
in int gl_SampleID;
#endif
#endif

VARYING(TEXCOORD0) in 	float4 	xrvary8		; // TEXCOORD0;
#ifdef 	USE_SJITTER
VARYING(TEXCOORD1) in 	float4 	xrvary9	; // TEXCOORD1;
#endif

#ifdef MSAA_OPTIMIZATION
#ifdef GBUFFER_OPTIMIZATION
float4 _main( p_volume I, float4 pos2d, uint iSample );
#else
float4 _main( p_volume I, uint iSample  );
#endif
#else
#ifdef GBUFFER_OPTIMIZATION
float4 _main( p_volume I, float4 pos2d );
#else
float4 _main( p_volume I );
#endif
#endif

void main()
{
	p_volume	I;
	I.tc		= xrvary8;
#ifdef 	USE_SJITTER
	I.tcJ 		= xrvary9;
#endif

#ifdef MSAA_OPTIMIZATION
#ifdef GBUFFER_OPTIMIZATION
	SV_Target	= _main( I, gl_FragCoord, gl_SampleID );
#else
	SV_Target	= _main( I, gl_SampleID  );
#endif
#else
#ifdef GBUFFER_OPTIMIZATION
	SV_Target	= _main( I, gl_FragCoord );
#else
	SV_Target	= _main( I );
#endif
#endif
}
