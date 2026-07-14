
out vec4 SV_Target;

VARYING(TEXCOORD0) in float2 	p_postpr_Tex0	; // TEXCOORD0;	// base1 (duality)	
VARYING(TEXCOORD1) in float2	p_postpr_Tex1	; // TEXCOORD1;	// base2 (duality)
VARYING(TEXCOORD2) in float2	p_postpr_Tex2	; // TEXCOORD2;	// base  (noise)
VARYING(COLOR0) in float4	p_postpr_Color	; // COLOR0;		// multiplier, color.w = noise_amount
VARYING(COLOR1) in float4	p_postpr_Gray	; // COLOR1;		// (.3,.3,.3.,amount)

float4 _main ( p_postpr I );

void main()
{
	p_postpr	I;
	I.Tex0		= p_postpr_Tex0;
	I.Tex1		= p_postpr_Tex1;
	I.Tex2		= p_postpr_Tex2;
	I.Color 	= p_postpr_Color;
	I.Gray 		= p_postpr_Gray;

	SV_Target	= _main (I);
}
