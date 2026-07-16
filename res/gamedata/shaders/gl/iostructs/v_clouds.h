
#ifndef GL_ES
out gl_PerVertex { vec4 gl_Position; };
#endif

struct 	vi
{
	float4	p		; // POSITION;
	float4	dir		; // COLOR0;	// dir0,dir1(w<->z)
	float4	color	; // COLOR1;	// rgb. intensity
};

struct 	vf
{
	float4	color	; // COLOR0;	// rgb. intensity, for SM3 - tonemap-prescaled, HI-res
  	float2	tc0		; // TEXCOORD0;
  	float2	tc1		; // TEXCOORD1;
	float4 	hpos	; // SV_Position;
};

layout(location = POSITION)		in float4	v_clouds_p		; // POSITION;
layout(location = COLOR0)		in float4	v_clouds_dir	; // COLOR0;	// dir0,dir1(w<->z)
layout(location = COLOR1)		in float4	v_clouds_color	; // COLOR1;	// rgb. intensity

VARYING(COLOR0) out float4	xrvary0	; // COLOR0;	// rgb. intensity, for SM3 - tonemap-prescaled, HI-res
VARYING(TEXCOORD0) out float2	xrvary8		; // TEXCOORD0;
VARYING(TEXCOORD1) out float2	xrvary9		; // TEXCOORD1;

vf _main (vi v);

void main()
{
	vi		I;
	I.p		= v_clouds_p;
	I.dir	= v_clouds_dir;
	I.color = v_clouds_color;

	vf O 	= _main (I);

	xrvary0 = O.color;
	xrvary8 = O.tc0;
	xrvary9 = O.tc1;
	gl_Position 	= O.hpos;
}
