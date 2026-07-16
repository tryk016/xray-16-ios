
out vec4 SV_Target;

VARYING(TEXCOORD1) in float2	xrvary9	; // TEXCOORD1;	// Diffuse map for aref

float4 _main ( p_shadow_direct_aref I );

void main()
{
	p_shadow_direct_aref I;
	I.tc0		= xrvary9;

	SV_Target	= _main (I);
}
