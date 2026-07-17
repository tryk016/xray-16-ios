
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

VARYING(TEXCOORD0) in float4 	xrvary8		; // TEXCOORD0;
VARYING(TEXCOORD1) in float4	xrvary9	; // TEXCOORD1;
VARYING(TEXCOORD2) in float4 	xrvary10		; // TEXCOORD2;
VARYING(TEXCOORD3) in float4	xrvary11		; // TEXCOORD3;
VARYING(TEXCOORD4) in float4 	xrvary12		; // TEXCOORD4;
VARYING(TEXCOORD5) in float4	xrvary13		; // TEXCOORD5; (VS writes float4 here; read .xy)

#ifdef MSAA_OPTIMIZATION
#ifdef GBUFFER_OPTIMIZATION
float4 	_main	( p_aa_AA_sun I, float4 pos2d, uint iSample );
#else
float4 	_main	( p_aa_AA_sun I, uint iSample );
#endif
#else
#ifdef GBUFFER_OPTIMIZATION
float4 	_main	( p_aa_AA_sun I, float4 pos2d );
#else
float4 	_main	( p_aa_AA_sun I );
#endif
#endif

void main()
{
	p_aa_AA_sun	I;
	I.tc		= xrvary8.xy;
	I.unused	= xrvary9.xy;
	I.LT		= xrvary10.xy;
	I.RT		= xrvary11.xy;
	I.LB		= xrvary12.xy;
	I.RB		= xrvary13.xy;

#ifdef MSAA_OPTIMIZATION
#ifdef GBUFFER_OPTIMIZATION
	SV_Target	= _main	( I, gl_FragCoord, gl_SampleID );
#else
	SV_Target	= _main	( I, gl_SampleID );
#endif
#else
#ifdef GBUFFER_OPTIMIZATION
	SV_Target	= _main	( I, gl_FragCoord );
#else
	SV_Target	= _main	( I );
#endif
#endif
}
