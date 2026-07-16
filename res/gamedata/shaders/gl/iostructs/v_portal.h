
#ifndef GL_ES
out gl_PerVertex { vec4 gl_Position; };
#endif

struct	v_vert
{
	float4 	pos	; // POSITION;	// (float,float,float,1)
	float4	color	; // COLOR0;	// (r,g,b,dir-occlusion)
};
struct 	v2p
{
	float4 c	; // COLOR0;
	float  fog	; // FOG;
	float4 hpos	; // SV_Position;
};

layout(location = POSITION)		in float4	v_portal_pos	; // POSITION;	// (float,float,float,1)
layout(location = COLOR0)		in float4	v_portal_color	; // COLOR0;	// (r,g,b,dir-occlusion)

VARYING(COLOR0) out float4	xrvary0	; // COLOR0;
VARYING(FOG) out float	xrvary7	; // FOG;

v2p _main ( v_vert I );

void main()
{
	v_vert		I;
	I.pos		= v_portal_pos;
	I.color		= v_portal_color;

	v2p O 		= _main (I);

	xrvary0 = O.c;
	xrvary7 = O.fog;
	gl_Position = O.hpos;
}
