
#ifndef GL_ES
out gl_PerVertex { vec4 gl_Position; };
#endif

layout(location = POSITIONT)	in float4	v_TL2uv_P		; // POSITIONT;
layout(location = TEXCOORD0)	in float2	v_TL2uv_Tex0	; // TEXCOORD0;
layout(location = TEXCOORD1)	in float2	v_TL2uv_Tex1	; // TEXCOORD1;
layout(location = COLOR)		in float4	v_TL2uv_Color	; // COLOR; 

VARYING(TEXCOORD0) out float4 	xrvary8	; // TEXCOORD0;
VARYING(TEXCOORD1) out float4	xrvary9	; // TEXCOORD1;
VARYING(COLOR) out float4	xrvary0	; // COLOR;

v2p_TL2uv _main ( v_TL2uv I );

void main()
{
	v_TL2uv		I;
	I.P			= v_TL2uv_P;
	I.Tex0		= v_TL2uv_Tex0;
	I.Tex1		= v_TL2uv_Tex1;
	I.Color 	= v_TL2uv_Color;

	v2p_TL2uv O 	= _main (I);

	xrvary8 = float4(O.Tex0, 0.0, 1.0);
	xrvary9 = float4(O.Tex1, 0.0, 1.0);
	xrvary0 = O.Color;
	gl_Position = O.HPos;
}
