
out vec4 SV_Target;

#if defined(USE_R2_STATIC_SUN) && !defined(USE_LM_HEMI)
VARYING(TEXCOORD0) in float4	xrvary8		; // TEXCOORD0;	// Texture coordinates,         w=sun_occlusion
#else
VARYING(TEXCOORD0) in float4	xrvary8		; // TEXCOORD0;	// Texture coordinates
#endif
VARYING(TEXCOORD1) in float4	xrvary9; // TEXCOORD1;	// position + hemi
VARYING(TEXCOORD2) in float4	xrvary10		; // TEXCOORD2;	// Eye-space normal        (for lighting)
#ifdef USE_TDETAIL
VARYING(TEXCOORD3) in float4	xrvary11	; // TEXCOORD3;	// d-bump
#endif
#ifdef USE_LM_HEMI
VARYING(TEXCOORD4) in float4	xrvary12		; // TEXCOORD4;	// lm-hemi
#endif

float4 	_main	( p_flat I );

void main()
{
	p_flat		I;
#if defined(USE_R2_STATIC_SUN) && !defined(USE_LM_HEMI)
	I.tcdh		= xrvary8;
#else
	I.tcdh		= xrvary8.xy;
#endif
	I.position 	= xrvary9;
	I.N			= xrvary10.xyz;
#ifdef USE_TDETAIL
	I.tcdbump 	= xrvary11.xy;
#endif
#ifdef USE_LM_HEMI
	I.lmh		= xrvary12.xy;
#endif

	SV_Target	= _main	( I );
}
