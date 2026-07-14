
out vec4 SV_Target;

struct 	v2p
{
	float4	color	; // COLOR0;	// rgb. intensity, for SM3 - tonemap prescaled
  	float2	tc0		; // TEXCOORD0;
  	float2	tc1		; // TEXCOORD1;
};

VARYING(COLOR0) in float4	p_clouds_color	; // COLOR0;	// rgb. intensity, for SM3 - tonemap prescaled
VARYING(TEXCOORD0) in float2	p_clouds_tc0	; // TEXCOORD0;
VARYING(TEXCOORD1) in float2	p_clouds_tc1	; // TEXCOORD1;

float4 	_main	( v2p I );

void main()
{
	v2p I;
	I.color = p_clouds_color;
	I.tc0 = p_clouds_tc0;
	I.tc1 = p_clouds_tc1;

	SV_Target 	= _main ( I );
}
