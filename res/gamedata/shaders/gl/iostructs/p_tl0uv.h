
out vec4 SV_Target;

VARYING(COLOR) in float4	xrvary0	; // COLOR;

float4 _main ( p_TL0uv I );

void main()
{
	p_TL0uv		I;
	I.Color 	= xrvary0;

	SV_Target = _main (I);
}
