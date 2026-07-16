
out vec4 SV_Target;
#ifdef GBUFFER_OPTIMIZATION
#ifndef GL_ES
in vec4 gl_FragCoord;
#endif
#endif // GBUFFER_OPTIMIZATION
#ifdef MSAA_OPTIMIZATION
#ifndef GL_ES
in int gl_SampleID;
#endif
#endif // MSAA_OPTIMIZATION

VARYING(TEXCOORD0) in 	float4 	xrvary8; // TEXCOORD0;
#ifdef USE_SJITTER
VARYING(TEXCOORD1) in 	float4 	xrvary9; // TEXCOORD1;
#endif // USE_SJITTER

#ifdef MSAA_OPTIMIZATION
float4 _main ( v2p_volume I, uint iSample );
#else
float4 _main ( v2p_volume I );
#endif

void main()
{
	v2p_volume	I;
	I.tc		= xrvary8;
#ifdef USE_SJITTER
	I.tcJ 		= xrvary9;
#endif // USE_SJITTER
#ifdef GBUFFER_OPTIMIZATION
	I.hpos	= gl_FragCoord;
#endif // GBUFFER_OPTIMIZATION

#ifdef MSAA_OPTIMIZATION
	SV_Target	= _main ( I, gl_SampleID );
#else
	SV_Target	= _main ( I );
#endif
}
