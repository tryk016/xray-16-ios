
#ifndef GL_ES
out gl_PerVertex { vec4 gl_Position; };
#endif

layout(location = POSITION)		in float4	v_tree_P		; // POSITION;		// (float,float,float,1)
layout(location = NORMAL)		in float4	v_tree_Nh		; // NORMAL;		// (nx,ny,nz)
layout(location = TANGENT)		in float3	v_tree_T		; // TANGENT;		// tangent
layout(location = BINORMAL)		in float3	v_tree_B		; // BINORMAL;		// binormal
layout(location = TEXCOORD0)	in float4	v_tree_tc		; // TEXCOORD0;	// (u,v,frac,???)

#if defined(USE_R2_STATIC_SUN) && !defined(USE_LM_HEMI)
VARYING(TEXCOORD0) out float4	xrvary8	; // TEXCOORD0;	// Texture coordinates,         w=sun_occlusion
#else
VARYING(TEXCOORD0) out float2	xrvary8	; // TEXCOORD0;	// Texture coordinates
#endif
VARYING(TEXCOORD1) out float4	xrvary9; // TEXCOORD1;	// position + hemi
VARYING(TEXCOORD2) out float3	xrvary10	; // TEXCOORD2;	// nmap 2 eye - 1
VARYING(TEXCOORD3) out float3	xrvary11	; // TEXCOORD3;	// nmap 2 eye - 2
VARYING(TEXCOORD4) out float3	xrvary12	; // TEXCOORD4;	// nmap 2 eye - 3
#ifdef USE_TDETAIL
VARYING(TEXCOORD5) out float2	xrvary13; // TEXCOORD5;	// d-bump
#endif
#ifdef USE_LM_HEMI
VARYING(TEXCOORD6) out float2	xrvary14	; // TEXCOORD6;	// lm-hemi
#endif

v2p_bumped 	_main 	(v_tree I);

void main()
{
	v_tree		I;
	I.P			= v_tree_P;
	I.Nh		= v_tree_Nh;
	I.T			= v_tree_T;
	I.B			= v_tree_B;
	I.tc		= v_tree_tc;

	v2p_bumped O = _main (I);

	xrvary8	= O.tcdh;
	xrvary9 = O.position;
	xrvary10 = O.M1;
	xrvary11 = O.M2;
	xrvary12 = O.M3;
#ifdef USE_TDETAIL
	xrvary13 = O.tcdbump;
#endif
#ifdef	USE_LM_HEMI
	xrvary14 = O.lmh;
#endif
	gl_Position = O.hpos;
}
