
out vec4 SV_Target;

#if defined(USE_R2_STATIC_SUN) && !defined(USE_LM_HEMI)
VARYING(TEXCOORD0) in float4	p_bumped_tcdh	; // TEXCOORD0;	// Texture coordinates,         w=sun_occlusion
#else
VARYING(TEXCOORD0) in float2	p_bumped_tcdh	; // TEXCOORD0;	// Texture coordinates
#endif
VARYING(TEXCOORD1) in float4	p_bumped_position; // TEXCOORD1;	// position + hemi
VARYING(TEXCOORD2) in float3	p_bumped_M1		; // TEXCOORD2;	// nmap 2 eye - 1
VARYING(TEXCOORD3) in float3	p_bumped_M2		; // TEXCOORD3;	// nmap 2 eye - 2
VARYING(TEXCOORD4) in float3	p_bumped_M3		; // TEXCOORD4;	// nmap 2 eye - 3
#ifdef USE_TDETAIL
VARYING(TEXCOORD5) in float2	p_bumped_tcdbump; // TEXCOORD5;	// d-bump
#endif
#ifdef USE_LM_HEMI
VARYING(TEXCOORD6) in float2	p_bumped_lmh	; // TEXCOORD6;	// lm-hemi
#endif

float4 	_main	( p_bumped I );

void main()
{
	p_bumped	I;
	I.tcdh		= p_bumped_tcdh;
	I.position 	= p_bumped_position;
	I.M1		= p_bumped_M1;
	I.M2	 	= p_bumped_M2;
	I.M3		= p_bumped_M3;
#ifdef USE_TDETAIL
	I.tcdbump 	= p_bumped_tcdbump;
#endif
#ifdef USE_LM_HEMI
	I.lmh		= p_bumped_lmh;
#endif

	SV_Target	= _main	( I );
}
