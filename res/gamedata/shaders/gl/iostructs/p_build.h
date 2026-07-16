
out vec4 SV_Target;

VARYING(TEXCOORD0) in float2 	xrvary8	; // TEXCOORD0;
VARYING(TEXCOORD1) in float2	xrvary9	; // TEXCOORD1;
VARYING(TEXCOORD2) in float2	xrvary10	; // TEXCOORD2;
VARYING(TEXCOORD3) in float2	xrvary11	; // TEXCOORD3;

float4 _main ( p_build I );

void main()
{
	p_build		I;
	I.Tex0		= xrvary8;
	I.Tex1		= xrvary9;
	I.Tex2		= xrvary10;
	I.Tex3		= xrvary11;

	SV_Target	= _main (I);
}
