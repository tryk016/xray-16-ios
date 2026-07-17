
out vec4 SV_Target;

struct v2p
{
 	float2 	tc0	; 	// TEXCOORD0;		// base
 	float2 	tc1	; 	// TEXCOORD1;		// lmap
	float2	tc2	; 	// TEXCOORD2;		// hemi
	float3	tc3	; 	// TEXCOORD3;		// env
  	float3	c0	; 	// COLOR0;
	float3	c1	; 	// COLOR1;
	float   fog	; 	// FOG;
};

VARYING(TEXCOORD0) in float4	xrvary8		; // TEXCOORD0;		// base
VARYING(TEXCOORD1) in float4	xrvary9		; // TEXCOORD1;		// lmap
VARYING(TEXCOORD2) in float4	xrvary10		; // TEXCOORD2;		// hemi
VARYING(TEXCOORD3) in float4	xrvary11		; // TEXCOORD3;		// env
VARYING(COLOR0) in float4	xrvary0		; // COLOR0;
VARYING(COLOR1) in float4	xrvary1		; // COLOR1;
VARYING(FOG) in float4	xrvary7		; // FOG;

float4 _main ( v2p I );

void main()
{
	v2p		I;
	I.tc0		= xrvary8.xy;
	I.tc1		= xrvary9.xy;
	I.tc2		= xrvary10.xy;
	I.tc3		= xrvary11.xyz;
	I.c0	 	= xrvary0.xyz;
	I.c1	 	= xrvary1.xyz;
	I.fog		= xrvary7.x;

	SV_Target	= _main (I);
}
