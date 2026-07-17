
#ifndef GL_ES
out gl_PerVertex { vec4 gl_Position; };
#endif

layout(location = POSITIONT)	in float4	v_build_P		; // POSITIONT;
layout(location = TEXCOORD0)	in float2	v_build_Tex0	; // TEXCOORD0;
layout(location = TEXCOORD1)	in float2	v_build_Tex1	; // TEXCOORD1;
layout(location = TEXCOORD2)	in float2 	v_build_Tex2	; // TEXCOORD2;
layout(location = TEXCOORD3)	in float2	v_build_Tex3	; // TEXCOORD3;

VARYING(TEXCOORD0) out float4 	xrvary8	; // TEXCOORD0;
VARYING(TEXCOORD1) out float4	xrvary9	; // TEXCOORD1;
VARYING(TEXCOORD2) out float4 	xrvary10	; // TEXCOORD2;
VARYING(TEXCOORD3) out float4	xrvary11	; // TEXCOORD3;

v2p_build _main ( v_build I );

void main()
{
	v_build		I;
	I.P			= v_build_P;
	I.Tex0		= v_build_Tex0;
	I.Tex1		= v_build_Tex1;
	I.Tex2		= v_build_Tex2;
	I.Tex3		= v_build_Tex3;

	v2p_build O = _main (I);

	xrvary8 = float4(O.Tex0, 0.0, 1.0);
	xrvary9 = float4(O.Tex1, 0.0, 1.0);
	xrvary10 = float4(O.Tex2, 0.0, 1.0);
	xrvary11 = float4(O.Tex3, 0.0, 1.0);
	gl_Position = O.HPos;
}
