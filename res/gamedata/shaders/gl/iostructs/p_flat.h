
layout(location = 0) out vec4 SV_Target0;
layout(location = 1) out vec4 SV_Target1;
#ifndef GBUFFER_OPTIMIZATION
layout(location = 2) out vec4 SV_Target2;
#endif
#ifdef EXTEND_F_DEFFER
out int gl_SampleMask[];
#endif
#ifdef	MSAA_ALPHATEST_DX10_1_ATOC
#ifndef GL_ES
in vec4 gl_FragCoord;
#endif
#endif

#if defined(USE_R2_STATIC_SUN) && !defined(USE_LM_HEMI)
VARYING(TEXCOORD0) in float4	xrvary8		; // TEXCOORD0;	// Texture coordinates,         w=sun_occlusion
#else
VARYING(TEXCOORD0) in float2	xrvary8		; // TEXCOORD0;	// Texture coordinates
#endif
VARYING(TEXCOORD1) in float4	xrvary9; // TEXCOORD1;	// position + hemi
VARYING(TEXCOORD2) in float3	xrvary10		; // TEXCOORD2;	// Eye-space normal        (for lighting)
#ifdef USE_TDETAIL
VARYING(TEXCOORD3) in float2	xrvary11	; // TEXCOORD3;	// d-bump
#endif
#ifdef USE_LM_HEMI
VARYING(TEXCOORD4) in float2	xrvary12		; // TEXCOORD4;	// lm-hemi
#endif

#ifdef	MSAA_ALPHATEST_DX10_1_ATOC
f_deffer 	_main	( p_flat I, float4 pos2d );
#else	//	MSAA_ALPHATEST_DX10_1_ATOC
f_deffer 	_main	( p_flat I );
#endif	//	MSAA_ALPHATEST_DX10_1_ATOC

void main()
{
	p_flat		I;
	I.tcdh		= xrvary8;
	I.position 	= xrvary9;
	I.N			= xrvary10;
#ifdef USE_TDETAIL
	I.tcdbump 	= xrvary11;
#endif
#ifdef USE_LM_HEMI
	I.lmh		= xrvary12;
#endif

#ifdef	MSAA_ALPHATEST_DX10_1_ATOC
	f_deffer O	= _main	( I, gl_FragCoord );
#else	//	MSAA_ALPHATEST_DX10_1_ATOC
	f_deffer O	= _main	( I );
#endif	//	MSAA_ALPHATEST_DX10_1_ATOC

	SV_Target0 = O.position;
#ifdef GBUFFER_OPTIMIZATION
	SV_Target1 = O.C;
#else
	SV_Target1 = O.Ne;
	SV_Target2 = O.C;
#endif
#ifdef EXTEND_F_DEFFER
	gl_SampleMask[0] = O.mask;
#endif
}
