
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

VARYING(TEXCOORD0) in float2	xrvary8		; // TEXCOORD0;		// base
VARYING(TEXCOORD1) in float2	xrvary9		; // TEXCOORD1;		// lmap
VARYING(TEXCOORD2) in float2	xrvary10		; // TEXCOORD2;		// hemi
VARYING(TEXCOORD3) in float3	xrvary11		; // TEXCOORD3;		// env
VARYING(COLOR0) in float3	xrvary0		; // COLOR0;
VARYING(COLOR1) in float3	xrvary1		; // COLOR1;
VARYING(FOG) in float	xrvary7		; // FOG;

float4 _main ( v2p I );

void main()
{
	v2p		I;
	I.tc0		= xrvary8;
	I.tc1		= xrvary9;
	I.tc2		= xrvary10;
	I.tc3		= xrvary11;
	I.c0	 	= xrvary0;
	I.c1	 	= xrvary1;
	I.fog		= xrvary7;

	SV_Target	= _main (I);
}
