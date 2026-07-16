out vec4 SV_Target;
#ifdef GBUFFER_OPTIMIZATION
#ifndef GL_ES
in vec4 gl_FragCoord;
#endif
#endif // GBUFFER_OPTIMIZATION

struct	p_TL2uv_msaa
{
	float2 	Tex0	; // TEXCOORD0;
	float2	Tex1	; // TEXCOORD1;
	float4	Color	; // COLOR;
#ifdef GBUFFER_OPTIMIZATION
	float4 	HPos	; // SV_Position;	// Clip-space position 	(for rasterization)
#endif // GBUFFER_OPTIMIZATION
};

VARYING(TEXCOORD0) in float2	xrvary8	; // TEXCOORD0;
VARYING(TEXCOORD1) in float2	xrvary9	; // TEXCOORD1;
VARYING(COLOR) in float4	xrvary0	; // COLOR;

float4 _main ( p_TL2uv_msaa I );

void main()
{
	p_TL2uv_msaa	I;
	I.Tex0		= xrvary8;
	I.Tex1		= xrvary9;
	I.Color		= xrvary0;
#ifdef GBUFFER_OPTIMIZATION
	I.HPos		= gl_FragCoord;
#endif // GBUFFER_OPTIMIZATION
	SV_Target	= _main (I);
}
