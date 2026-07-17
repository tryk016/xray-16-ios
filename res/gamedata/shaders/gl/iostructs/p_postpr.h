
out vec4 SV_Target;

VARYING(TEXCOORD0) in float4 	xrvary8	; // TEXCOORD0;	// base1 (duality)	
VARYING(TEXCOORD1) in float4	xrvary9	; // TEXCOORD1;	// base2 (duality)
VARYING(TEXCOORD2) in float4	xrvary10	; // TEXCOORD2;	// base  (noise)
VARYING(COLOR0) in float4	xrvary0	; // COLOR0;		// multiplier, color.w = noise_amount
VARYING(COLOR1) in float4	xrvary1	; // COLOR1;		// (.3,.3,.3.,amount)

float4 _main ( p_postpr I );

void main()
{
	p_postpr	I;
	I.Tex0		= xrvary8.xy;
	I.Tex1		= xrvary9.xy;
	I.Tex2		= xrvary10.xy;
	I.Color 	= xrvary0;
	I.Gray 		= xrvary1;

	SV_Target	= _main (I);
}
