
out vec4 SV_Target;

struct v2p
{
	float2 Tex0	; // TEXCOORD0;
	float3 c0	; // COLOR0;		// c0=all lighting
	float  fog	; // FOG;
};

VARYING(TEXCOORD0) in float4	xrvary8		; // TEXCOORD0;
VARYING(COLOR0) in float4	xrvary0		; // COLOR0;		// c0=all lighting
VARYING(FOG) in float4	xrvary7		; // FOG;

float4 _main ( v2p I );

void main()
{
	v2p		I;
	I.Tex0		= xrvary8.xy;
	I.c0	 	= xrvary0.xyz;
	I.fog		= xrvary7.x;

	SV_Target	= _main (I);
}
