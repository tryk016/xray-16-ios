
layout(location = 0) out vec4 SV_Target0;
#ifndef	ATOC
layout(location = 1) out vec4 SV_Target1;
#ifndef GBUFFER_OPTIMIZATION
layout(location = 2) out vec4 SV_Target2;
#endif // GBUFFER_OPTIMIZATION
#ifdef EXTEND_F_DEFFER
out int gl_SampleMask[];
#endif // EXTEND_F_DEFFER
#ifdef	MSAA_ALPHATEST_DX10_1_ATOC
#ifndef GL_ES
in vec4 gl_FragCoord;
#endif
#endif // MSAA_ALPHATEST_DX10_1_ATOC
#endif // #endif 

struct v2p
{
	float3	Pe	; 	// TEXCOORD0;
 	float2 	tc0	; 	// TEXCOORD1;		// base0
 	float2 	tc1	; 	// TEXCOORD2;		// base1
	float4 	af	; 	// COLOR1;		// alpha&factor //skyloader: COLOR1? maybe COLOR0?
};

VARYING(TEXCOORD0) in float3	xrvary8		; // TEXCOORD0;		// base
VARYING(TEXCOORD1) in float2	xrvary9		; // TEXCOORD1;		// lmap
VARYING(TEXCOORD2) in float2	xrvary10		; // TEXCOORD2;		// hemi
VARYING(COLOR1) in float4	xrvary1		; // COLOR1;

#ifdef	ATOC
float4 _main ( v2p I );
#else	// ATOC
#ifdef	MSAA_ALPHATEST_DX10_1_ATOC
f_deffer _main ( v2p I, float4 pos2d );
#else	//	MSAA_ALPHATEST_DX10_1_ATOC
f_deffer _main ( v2p I );
#endif	//	MSAA_ALPHATEST_DX10_1_ATOC
#endif	// ATOC

void main()
{
	v2p		I;
	I.Pe		= xrvary8;
	I.tc0		= xrvary9;
	I.tc1		= xrvary10;
	I.af		= xrvary1;

#ifdef	ATOC
	SV_Target	= _main (I);
#else	// ATOC
#ifdef	MSAA_ALPHATEST_DX10_1_ATOC
	f_deffer O	= _main (I, gl_FragCoord);
#else
	f_deffer O	= _main (I);
#endif	// MSAA_ALPHATEST_DX10_1_ATOC

#endif	// ATOC

	SV_Target0 = O.position;
#ifdef GBUFFER_OPTIMIZATION
	SV_Target1 = O.C;
#else
	SV_Target1 = O.Ne;
	SV_Target2 = O.C;
#endif	// GBUFFER_OPTIMIZATION
#ifdef EXTEND_F_DEFFER
	gl_SampleMask[0] = O.mask;
#endif
}
