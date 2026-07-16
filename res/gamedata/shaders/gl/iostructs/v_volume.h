
#ifndef GL_ES
out gl_PerVertex { vec4 gl_Position; };
#endif

layout(location = POSITION)		in float4	v_volume_P;

VARYING(TEXCOORD0) out float4 	xrvary8	; // TEXCOORD0;
#ifdef 	USE_SJITTER
VARYING(TEXCOORD1) out float4 	xrvary9	; // TEXCOORD1;
#endif

v2p_volume _main ( float4 P );

void main()
{
	v2p_volume O	= _main ( v_volume_P );
	xrvary8	= O.tc;
#ifdef 	USE_SJITTER
	xrvary9	= O.tcJ;
#endif
	gl_Position		= O.hpos;
}
