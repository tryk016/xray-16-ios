
#ifndef GL_ES
out gl_PerVertex { vec4 gl_Position; };
#endif

layout(location = POSITIONT)	in float4	v_postpr_P		; // POSITIONT;
layout(location = TEXCOORD0)	in float2 	v_postpr_Tex0	; // TEXCOORD0;	// base1 (duality)	
layout(location = TEXCOORD1)	in float2	v_postpr_Tex1	; // TEXCOORD1;	// base2 (duality)
layout(location = TEXCOORD2)	in float2	v_postpr_Tex2	; // TEXCOORD2;	// base  (noise)
layout(location = COLOR0)		in float4	v_postpr_Color	; // COLOR0;		// multiplier, color.w = noise_amount
layout(location = COLOR1)		in float4	v_postpr_Gray	; // COLOR1;		// (.3,.3,.3.,amount)

VARYING(TEXCOORD0) out float4 	xrvary8	; // TEXCOORD0;	// base1 (duality)	
VARYING(TEXCOORD1) out float4	xrvary9	; // TEXCOORD1;	// base2 (duality)
VARYING(TEXCOORD2) out float4	xrvary10	; // TEXCOORD2;	// base  (noise)
VARYING(COLOR0) out float4	xrvary0; // COLOR0;		// multiplier, color.w = noise_amount
VARYING(COLOR1) out float4	xrvary1	; // COLOR1;		// (.3,.3,.3.,amount)

v2p_postpr _main ( v_postpr I );

void main()
{
	v_postpr	I;
	I.P			= v_postpr_P;
	I.Tex0		= v_postpr_Tex0;
	I.Tex1 		= v_postpr_Tex1;
	I.Tex2 		= v_postpr_Tex2;
	I.Color		= v_postpr_Color;
	I.Gray 		= v_postpr_Gray;

	v2p_postpr O = _main (I);

	xrvary8 = float4(O.Tex0, 0.0, 1.0);
	xrvary9 = float4(O.Tex1, 0.0, 1.0);
	xrvary10 = float4(O.Tex2, 0.0, 1.0);
	xrvary0 = O.Color;
	xrvary1 = O.Gray;
	gl_Position = O.HPos;
}
