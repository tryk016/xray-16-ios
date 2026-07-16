
#ifndef GL_ES
out gl_PerVertex { vec4 gl_Position; };
#endif

struct vf
{
	float4 C	; // COLOR0;
	float4 P	; // POSITION;
};

struct v2p
{
	float4 C	; // COLOR0;
	float4 P	; // SV_Position;
};

layout(location = COLOR0)		in float4 v_editor_C	; // COLOR0;
layout(location = POSITION)		in float4 v_editor_P	; // POSITION;

VARYING(COLOR0) out float4 xrvary0	; // COLOR0;

v2p _main (vf i);

void main()
{
	vf			I;
	I.C			= v_editor_C;
	I.P			= v_editor_P;

	v2p O 		= _main (I);

	xrvary0 = O.C;
	gl_Position = O.P;
}
