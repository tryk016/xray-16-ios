
out vec4 SV_Target;

VARYING(TEXCOORD0) in float2	xrvary8	; // TEXCOORD0;
VARYING(COLOR) in float4	xrvary0	; // COLOR; 

float4 _main ( p_TL I );

void main()
{
	p_TL		I;
	I.Tex0		= xrvary8;
	I.Color 	= xrvary0;

	SV_Target	= _main (I);
}
