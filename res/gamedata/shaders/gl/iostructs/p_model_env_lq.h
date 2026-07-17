
out vec4 SV_Target;

struct v2p
{
	float2  tc0	; // TEXCOORD0;		// base
	float3  tc1	; // TEXCOORD1;		// environment
	float3  c0	; // COLOR0;		// sun
	float   fog	; // FOG;
};

VARYING(TEXCOORD0) in float4	xrvary8		; // TEXCOORD0;
VARYING(TEXCOORD1) in float4	xrvary9		; // TEXCOORD1;
VARYING(COLOR0) in float4	xrvary0		; // COLOR0;		
VARYING(FOG) in float4	xrvary7		; // FOG;

float4 _main ( v2p I );

void main()
{
	v2p		I;
	I.tc0		= xrvary8.xy;
	I.tc1		= xrvary9.xyz;
	I.c0		= xrvary0.xyz;
	I.fog		= xrvary7.x;

	SV_Target	= _main (I);
}
