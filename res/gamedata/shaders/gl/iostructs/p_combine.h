
layout(location = 0) out vec4 SV_Target0;
layout(location = 1) out vec4 SV_Target1;
#ifndef GL_ES
in vec4 gl_FragCoord;
#endif

#ifdef MSAA_OPTIMIZATION
#ifndef GL_ES
in int gl_SampleID;
#endif
#endif

struct	_input
{
#ifdef USE_VTF
	float4	tc0	; // TEXCOORD0;	// tc.xy, tc.w = tonemap scale
#else // USE_VTF
	float2	tc0	; // TEXCOORD0;	// tc.xy
#endif // USE_VTF
	float2	tcJ	; // TEXCOORD1;	// jitter coords
	float4	pos2d	; // SV_Position;
};

struct	_out
{
	float4	low	; // SV_Target0;
	float4	high	; // SV_Target1;
};

#ifdef USE_VTF
VARYING(TEXCOORD0) in float4	xrvary8	; // TEXCOORD0;	// tc.xy, tc.w = tonemap scale
#else // USE_VTF
VARYING(TEXCOORD0) in float2	xrvary8	; // TEXCOORD0;	// tc.xy
#endif // USE_VTF
VARYING(TEXCOORD1) in float2	xrvary9	; // TEXCOORD1;	// jitter coords

#ifndef MSAA_OPTIMIZATION
_out _main ( _input I );
#else
_out _main ( _input I, uint iSample );
#endif

void main()
{
	_input		I;
	I.tc0 		= xrvary8;
	I.tcJ 		= xrvary9;
	I.pos2d		= gl_FragCoord;

#ifndef MSAA_OPTIMIZATION
	_out O		= _main ( I );
#else
	_out O		= _main ( I, gl_SampleID );
#endif

	SV_Target0	= O.low;
	SV_Target1	= O.high;
}
