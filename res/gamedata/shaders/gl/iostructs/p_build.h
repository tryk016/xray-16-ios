
out vec4 SV_Target;

VARYING(TEXCOORD0) in float2 	p_build_Tex0	; // TEXCOORD0;
VARYING(TEXCOORD1) in float2	p_build_Tex1	; // TEXCOORD1;
VARYING(TEXCOORD2) in float2	p_build_Tex2	; // TEXCOORD2;
VARYING(TEXCOORD3) in float2	p_build_Tex3	; // TEXCOORD3;

float4 _main ( p_build I );

void main()
{
	p_build		I;
	I.Tex0		= p_build_Tex0;
	I.Tex1		= p_build_Tex1;
	I.Tex2		= p_build_Tex2;
	I.Tex3		= p_build_Tex3;

	SV_Target	= _main (I);
}
