
out vec4 SV_Target;

struct v2p
{
 	float2 	tc0	; // TEXCOORD0;		// base
  	float4	c0	; // COLOR0;		// sun
};

VARYING(TEXCOORD0) in float2	xrvary8		; // TEXCOORD0;		// base
VARYING(COLOR0) in float4	xrvary0		; // COLOR0;		// sun

float4 _main ( v2p I );

void main()
{
	v2p		I;
	I.tc0		= xrvary8;
	I.c0	 	= xrvary0;

	SV_Target	= _main (I);
}
