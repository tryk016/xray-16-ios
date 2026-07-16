
out vec4 SV_Target;

struct 	v2p
{
	float4	color	; // COLOR0;	// rgb. intensity, for SM3 - tonemap prescaled
  	float2	tc0		; // TEXCOORD0;
  	float2	tc1		; // TEXCOORD1;
};

VARYING(COLOR0) in float4	xrvary0	; // COLOR0;	// rgb. intensity, for SM3 - tonemap prescaled
VARYING(TEXCOORD0) in float2	xrvary8	; // TEXCOORD0;
VARYING(TEXCOORD1) in float2	xrvary9	; // TEXCOORD1;

float4 	_main	( v2p I );

void main()
{
	v2p I;
	I.color = xrvary0;
	I.tc0 = xrvary8;
	I.tc1 = xrvary9;

	SV_Target 	= _main ( I );
}
