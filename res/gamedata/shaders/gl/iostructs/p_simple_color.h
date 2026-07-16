
out vec4 SV_Target;

VARYING(COLOR0) in float4	xrvary0	; // COLOR0; 

float4 _main ( float4 C );

void main()
{
	SV_Target	= _main ( xrvary0 );
}
