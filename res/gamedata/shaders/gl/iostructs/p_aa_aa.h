
out vec4 SV_Target;

VARYING(TEXCOORD0) in float2 	xrvary8	; // TEXCOORD0;
VARYING(TEXCOORD1) in float2	xrvary9	; // TEXCOORD1;
VARYING(TEXCOORD2) in float2 	xrvary10	; // TEXCOORD2;
VARYING(TEXCOORD3) in float2	xrvary11	; // TEXCOORD3;
VARYING(TEXCOORD4) in float2 	xrvary12	; // TEXCOORD4;
VARYING(TEXCOORD5) in float4	xrvary13	; // TEXCOORD5;
VARYING(TEXCOORD6) in float4 	xrvary14	; // TEXCOORD6;

float4 _main ( p_aa_AA I );

void main()
{
	p_aa_AA	I;
	I.Tex0		= xrvary8;
	I.Tex1		= xrvary9;
	I.Tex2		= xrvary10;
	I.Tex3		= xrvary11;
	I.Tex4		= xrvary12;
	I.Tex5		= xrvary13;
	I.Tex6		= xrvary14;

	SV_Target	= _main (I);
}
