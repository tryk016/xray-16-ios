
#ifndef GL_ES
out gl_PerVertex { vec4 gl_Position; };
#endif

layout(location = POSITION)		in float4	v_TL_P		; // POSITION;
layout(location = TEXCOORD0)	in float2	v_TL_Tex0	; // TEXCOORD0;
layout(location = COLOR)		in float4	v_TL_Color	; // COLOR; 

VARYING(TEXCOORD0) out float2 	xrvary8	; // TEXCOORD0;
VARYING(COLOR) out float4	xrvary0; // COLOR;

v2p_TL _main ( v_TL I );

void main()
{
	v_TL		I;
	I.P			= v_TL_P;
	I.Tex0		= v_TL_Tex0;
	I.Color 	= v_TL_Color;

	v2p_TL O 	= _main (I);

	xrvary8	= O.Tex0;
	xrvary0 = O.Color;
	gl_Position = O.HPos;
}
