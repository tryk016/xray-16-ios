
out vec4 SV_Target;

#if defined(USE_R2_STATIC_SUN) && !defined(USE_LM_HEMI)
VARYING(TEXCOORD0) in float4	xrvary8	; // TEXCOORD0;	// Texture coordinates,         w=sun_occlusion
#else
VARYING(TEXCOORD0) in float4	xrvary8	; // TEXCOORD0;	// Texture coordinates
#endif
VARYING(TEXCOORD1) in float4	xrvary9; // TEXCOORD1;	// position + hemi
VARYING(TEXCOORD2) in float4	xrvary10		; // TEXCOORD2;	// nmap 2 eye - 1
VARYING(TEXCOORD3) in float4	xrvary11		; // TEXCOORD3;	// nmap 2 eye - 2
VARYING(TEXCOORD4) in float4	xrvary12		; // TEXCOORD4;	// nmap 2 eye - 3
#ifdef USE_TDETAIL
VARYING(TEXCOORD5) in float4	xrvary13; // TEXCOORD5;	// d-bump
#endif
#ifdef USE_LM_HEMI
VARYING(TEXCOORD6) in float4	xrvary14	; // TEXCOORD6;	// lm-hemi
#endif

float4 	_main	( p_bumped I );

void main()
{
	p_bumped	I;
#if defined(USE_R2_STATIC_SUN) && !defined(USE_LM_HEMI)
	I.tcdh		= xrvary8;
#else
	I.tcdh		= xrvary8.xy;
#endif
	I.position 	= xrvary9;
	I.M1		= xrvary10.xyz;
	I.M2	 	= xrvary11.xyz;
	I.M3		= xrvary12.xyz;
#ifdef USE_TDETAIL
	I.tcdbump 	= xrvary13.xy;
#endif
#ifdef USE_LM_HEMI
	I.lmh		= xrvary14.xy;
#endif

	SV_Target	= _main	( I );
}
