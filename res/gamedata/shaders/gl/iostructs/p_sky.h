
layout(location = 0) out vec4 SV_Target0;
layout(location = 1) out vec4 SV_Target1;

struct         v2p
{
	float4	factor	; // COLOR0;        // for SM3 - factor.rgb - tonemap-prescaled
	float3	tc0		; // TEXCOORD0;
	float3	tc1		; // TEXCOORD1;
};
struct        _out
{
	float4	low		; // SV_Target0;
	float4	high	; // SV_Target1;
};

VARYING(COLOR0) in float4	xrvary0; // COLOR0;        // for SM3 - factor.rgb - tonemap-prescaled
VARYING(TEXCOORD0) in float3	xrvary8	; // TEXCOORD0;
VARYING(TEXCOORD1) in float3	xrvary9	; // TEXCOORD1;

_out _main( v2p I );

void main()
{
	v2p			I;
	I.factor	= xrvary0;
	I.tc0 		= xrvary8;
	I.tc1 		= xrvary9;

	_out O		= _main (I);

	SV_Target0	= O.low;
	SV_Target1	= O.high;
}
