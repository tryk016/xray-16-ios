
#ifndef GL_ES
out gl_PerVertex
{
	vec4 gl_Position;
	float gl_ClipDistance[6];
};
#endif

struct v2p
{
	float3 	lightToPos	; // TEXCOORD0;		// light center to plane vector
	float3 	vPos		; // TEXCOORD1;		// position in camera space
	float 	fDensity	; // TEXCOORD2;		// plane density alon Z axis
//	float2	tNoise 		; // TEXCOORD3;		// projective noise
	float3	clip0		; // SV_ClipDistance0;
	float3	clip1		; // SV_ClipDistance1;
	float4 	hpos		; // SV_Position;
};

layout(location = POSITION)		in float3	v_volumetric_P;

VARYING(TEXCOORD0) out float3 	xrvary8	; // TEXCOORD0;		// light center to plane vector
VARYING(TEXCOORD1) out float3 	xrvary9		; // TEXCOORD1;		// position in camera space
VARYING(TEXCOORD2) out float 	xrvary10	; // TEXCOORD2;		// plane density alon Z axis
//VARYING(TEXCOORD3) out float2	xrvary11 		; // TEXCOORD3;		// projective noise

v2p _main ( float3 P );

void main()
{
	v2p O	= _main ( v_volumetric_P );
	xrvary8	= O.lightToPos;
	xrvary9		= O.vPos;
	xrvary10	= O.fDensity;
//	xrvary11		= O.tNoise;
	gl_Position		= O.hpos;
	// gl_ClipDistance is a desktop/EXT_clip_cull_distance built-in; GLSL ES 3.00 has no
	// hardware clip planes. Skip writing them on ES (the light volume just isn't frustum-
	// clipped in the vertex stage — minor overdraw, not a failure). Matches the gl_PerVertex
	// guard above that also drops the gl_ClipDistance[6] declaration on ES.
#ifndef GL_ES
	for (int i=0; i<3; ++i)
	{
		gl_ClipDistance[i] = O.clip0[i];
		gl_ClipDistance[i+3] = O.clip1[i];
	}
#endif
}
