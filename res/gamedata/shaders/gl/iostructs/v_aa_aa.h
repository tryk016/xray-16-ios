
#ifndef GL_ES
out gl_PerVertex { vec4 gl_Position; };
#endif

layout(location = POSITIONT)	in float4 	v_aa_AA_P		; // POSITIONT;
layout(location = TEXCOORD0)	in float2 	v_aa_AA_Tex0	; // TEXCOORD0;
layout(location = TEXCOORD1)	in float2	v_aa_AA_Tex1	; // TEXCOORD1;
layout(location = TEXCOORD2)	in float2 	v_aa_AA_Tex2	; // TEXCOORD2;
layout(location = TEXCOORD3)	in float2	v_aa_AA_Tex3	; // TEXCOORD3;
layout(location = TEXCOORD4)	in float2	v_aa_AA_Tex4	; // TEXCOORD4;
layout(location = TEXCOORD5)	in float4	v_aa_AA_Tex5	; // TEXCOORD5;
layout(location = TEXCOORD6)	in float4	v_aa_AA_Tex6	; // TEXCOORD6;

VARYING(TEXCOORD0) out float2 	xrvary8	; // TEXCOORD0;
VARYING(TEXCOORD1) out float2	xrvary9	; // TEXCOORD1;
VARYING(TEXCOORD2) out float2 	xrvary10	; // TEXCOORD2;
VARYING(TEXCOORD3) out float2	xrvary11	; // TEXCOORD3;
VARYING(TEXCOORD4) out float2	xrvary12	; // TEXCOORD4;
VARYING(TEXCOORD5) out float4	xrvary13	; // TEXCOORD5;
VARYING(TEXCOORD6) out float4	xrvary14	; // TEXCOORD6;

v2p_aa_AA _main ( v_aa_AA I );

void main()
{
	v_aa_AA		I;
	I.P			= v_aa_AA_P;
	I.Tex0		= v_aa_AA_Tex0;
	I.Tex1		= v_aa_AA_Tex1;
	I.Tex2		= v_aa_AA_Tex2;
	I.Tex3		= v_aa_AA_Tex3;
	I.Tex4		= v_aa_AA_Tex4;
	I.Tex5		= v_aa_AA_Tex5;
	I.Tex6		= v_aa_AA_Tex6;

	v2p_aa_AA O = _main (I);

	xrvary8 = O.Tex0;
	xrvary9 = O.Tex1;
	xrvary10 = O.Tex2;
	xrvary11 = O.Tex3;
	xrvary12 = O.Tex4;
	xrvary13 = O.Tex5;
	xrvary14 = O.Tex6;
	gl_Position = O.HPos;
}
