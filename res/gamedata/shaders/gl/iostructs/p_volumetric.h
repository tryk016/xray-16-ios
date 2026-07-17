
out vec4 SV_Target;
#ifdef MSAA_OPTIMIZATION
#ifndef GL_ES
in int gl_SampleID;
#endif
#endif

struct 	v2p
{
	float3 	lightToPos	; // TEXCOORD0;	// light center to plane vector
	float3 	vPos		; // TEXCOORD1;	// position in camera space
	float 	fDensity	; // TEXCOORD2;	// plane density along Z axis
//	float2	tNoise 		; // TEXCOORD3;	// projective noise
};

VARYING(TEXCOORD0) in float4 	xrvary8	; // TEXCOORD0;		// light center to plane vector
VARYING(TEXCOORD1) in float4 	xrvary9		; // TEXCOORD1;		// position in camera space
VARYING(TEXCOORD2) in float4 	xrvary10	; // TEXCOORD2;		// plane density along Z axis
//VARYING(TEXCOORD3) in float2	xrvary11 		; // TEXCOORD3;		// projective noise

#ifdef MSAA_OPTIMIZATION
float4 _main ( v2p I, uint iSample );
#else
float4 _main ( v2p I );
#endif

void main()
{
	v2p	I;
	I.lightToPos = xrvary8.xyz;
	I.vPos		= xrvary9.xyz;
	I.fDensity	= xrvary10.x;
//	I.tNoise	= xrvary11;

#ifdef MSAA_OPTIMIZATION
	SV_Target	= _main ( I, gl_SampleID );
#else
	SV_Target	= _main ( I );
#endif
}
