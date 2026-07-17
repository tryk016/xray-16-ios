
out vec4 SV_Target;

struct 	v2p
{
	float2	tc0	; // TEXCOORD0;	// base
};

VARYING(TEXCOORD0) in float4	xrvary8	; // TEXCOORD0;	// base

float4 _main ( v2p I );

void main()
{
	v2p			I;
	I.tc0		= xrvary8.xy;

	SV_Target	= _main ( I );
}
