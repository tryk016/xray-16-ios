
out vec4 SV_Target;

VARYING(TEXCOORD0) in float4 	xrvary8	; // TEXCOORD0;
VARYING(TEXCOORD1) in float4	xrvary9	; // TEXCOORD1;
VARYING(TEXCOORD2) in float4	xrvary10	; // TEXCOORD2;
VARYING(TEXCOORD3) in float4	xrvary11	; // TEXCOORD3;

float4 _main ( p_build I );

void main()
{
	p_build		I;
	I.Tex0		= xrvary8.xy;
	I.Tex1		= xrvary9.xy;
	I.Tex2		= xrvary10.xy;
	I.Tex3		= xrvary11.xy;

	SV_Target	= _main (I);
}
