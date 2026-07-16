
#ifndef GL_ES
out gl_PerVertex { vec4 gl_Position; };
#endif

layout(location = POSITIONT)	in float4	v_filter_P		; // POSITIONT;
layout(location = TEXCOORD0)	in float4 	v_filter_Tex0	; // TEXCOORD0;
layout(location = TEXCOORD1)	in float4	v_filter_Tex1	; // TEXCOORD1;
layout(location = TEXCOORD2)	in float4 	v_filter_Tex2	; // TEXCOORD2;
layout(location = TEXCOORD3)	in float4	v_filter_Tex3	; // TEXCOORD3;
layout(location = TEXCOORD4)	in float4 	v_filter_Tex4	; // TEXCOORD4;
layout(location = TEXCOORD5)	in float4	v_filter_Tex5	; // TEXCOORD5;
layout(location = TEXCOORD6)	in float4 	v_filter_Tex6	; // TEXCOORD6;
layout(location = TEXCOORD7)	in float4	v_filter_Tex7	; // TEXCOORD7;

VARYING(TEXCOORD0) out float4 	xrvary8	; // TEXCOORD0;
VARYING(TEXCOORD1) out float4	xrvary9	; // TEXCOORD1;
VARYING(TEXCOORD2) out float4 	xrvary10	; // TEXCOORD2;
VARYING(TEXCOORD3) out float4	xrvary11	; // TEXCOORD3;
VARYING(TEXCOORD4) out float4 	xrvary12	; // TEXCOORD4;
VARYING(TEXCOORD5) out float4	xrvary13	; // TEXCOORD5;
VARYING(TEXCOORD6) out float4 	xrvary14	; // TEXCOORD6;
VARYING(TEXCOORD7) out float4	xrvary15	; // TEXCOORD7;

v2p_filter _main ( v_filter I );

void main()
{
	v_filter	I;
	I.P			= v_filter_P;
	I.Tex0		= v_filter_Tex0;
	I.Tex1		= v_filter_Tex1;
	I.Tex2		= v_filter_Tex2;
	I.Tex3		= v_filter_Tex3;
	I.Tex4		= v_filter_Tex4;
	I.Tex5		= v_filter_Tex5;
	I.Tex6		= v_filter_Tex6;
	I.Tex7		= v_filter_Tex7;

	v2p_filter O = _main (I);

	xrvary8 = O.Tex0;
	xrvary9 = O.Tex1;
	xrvary10 = O.Tex2;
	xrvary11 = O.Tex3;
	xrvary12 = O.Tex4;
	xrvary13 = O.Tex5;
	xrvary14 = O.Tex6;
	xrvary15 = O.Tex7;
	gl_Position = O.HPos;
}
