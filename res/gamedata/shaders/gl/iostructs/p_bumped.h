
out vec4 SV_Target0;
out vec4 SV_Target1;
#ifndef GBUFFER_OPTIMIZATION
out vec4 SV_Target2;
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
VARYING(TEXCOORD0) in float4	xrvary8	; // TEXCOORD0;	// Texture coordinates,         w=sun_occlusion
#else
VARYING(TEXCOORD0) in float2	xrvary8	; // TEXCOORD0;	// Texture coordinates
#endif
VARYING(TEXCOORD1) in float4	xrvary9; // TEXCOORD1;	// position + hemi
VARYING(TEXCOORD2) in float3	xrvary10		; // TEXCOORD2;	// nmap 2 eye - 1
VARYING(TEXCOORD3) in float3	xrvary11		; // TEXCOORD3;	// nmap 2 eye - 2
VARYING(TEXCOORD4) in float3	xrvary12		; // TEXCOORD4;	// nmap 2 eye - 3
#ifdef USE_TDETAIL
VARYING(TEXCOORD5) in float2	xrvary13; // TEXCOORD5;	// d-bump
#endif
#ifdef USE_LM_HEMI
VARYING(TEXCOORD6) in float2	xrvary14	; // TEXCOORD6;	// lm-hemi
#endif

#ifdef	MSAA_ALPHATEST_DX10_1_ATOC
f_deffer 	_main	( p_bumped I, float4 pos2d );
#else	//	MSAA_ALPHATEST_DX10_1_ATOC
f_deffer 	_main	( p_bumped I );
#endif	//	MSAA_ALPHATEST_DX10_1_ATOC

void main()
{
	p_bumped	I;
	I.tcdh		= xrvary8;
	I.position 	= xrvary9;
	I.M1		= xrvary10;
	I.M2	 	= xrvary11;
	I.M3		= xrvary12;
#ifdef USE_TDETAIL
	I.tcdbump 	= xrvary13;
#endif
#ifdef USE_LM_HEMI
	I.lmh		= xrvary14;
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
