
#ifndef GL_ES
out gl_PerVertex { vec4 gl_Position; };
#endif

layout(location = POSITION)		in float4	v_model_P		; // POSITION;		// (float,float,float,1)
#if defined(SKIN_0) || defined(SKIN_NONE)
layout(location = NORMAL)		in float3	v_model_N		; // NORMAL;		// (nx,ny,nz)
#else
layout(location = NORMAL)		in float4	v_model_N		; // NORMAL;		// (nx,ny,nz,index)
#endif
#if defined(SKIN_3) || defined(SKIN_4)
layout(location = TANGENT)		in float4	v_model_T		; // TANGENT;		// (nx,ny,nz,weight0)
layout(location = BINORMAL)		in float4	v_model_B		; // BINORMAL;		// (nx,ny,nz,weight1)
#else
layout(location = TANGENT)		in float3	v_model_T		; // TANGENT;		// (nx,ny,nz)
layout(location = BINORMAL)		in float3	v_model_B		; // BINORMAL;		// (nx,ny,nz)
#endif
#if defined(SKIN_2) || defined(SKIN_3)
layout(location = TEXCOORD0)	in float4	v_model_tc		; // TEXCOORD0;	// (u,v)
#else
layout(location = TEXCOORD0)	in float2	v_model_tc		; // TEXCOORD0;	// (u,v)
#endif
#ifdef	SKIN_4
layout(location = TEXCOORD1)	in float4	v_model_ind		; // (x=m-index0, y=m-index1, z=m-index2, w=m-index3)
#endif

#if defined(USE_R2_STATIC_SUN) && !defined(USE_LM_HEMI)
VARYING(TEXCOORD0) out float4	xrvary8	; // TEXCOORD0;	// Texture coordinates,         w=sun_occlusion
#else
VARYING(TEXCOORD0) out float4	xrvary8	; // TEXCOORD0;	// Texture coordinates
#endif
VARYING(TEXCOORD1) out float4	xrvary9; // TEXCOORD1;	// position + hemi
VARYING(TEXCOORD2) out float4	xrvary10	; // TEXCOORD2;	// nmap 2 eye - 1
VARYING(TEXCOORD3) out float4	xrvary11	; // TEXCOORD3;	// nmap 2 eye - 2
VARYING(TEXCOORD4) out float4	xrvary12	; // TEXCOORD4;	// nmap 2 eye - 3
#ifdef USE_TDETAIL
VARYING(TEXCOORD5) out float4	xrvary13; // TEXCOORD5;	// d-bump
#endif
#ifdef USE_LM_HEMI
VARYING(TEXCOORD6) out float4	xrvary14	; // TEXCOORD6;	// lm-hemi
#endif

v2p_bumped _main( v_model I );

void main()
{
#ifdef 	SKIN_NONE
	v_model I;
#endif
#ifdef 	SKIN_0
	v_model_skinned_0 I;
#endif
#ifdef	SKIN_1
	v_model_skinned_1 I;
#endif
#ifdef	SKIN_2
	v_model_skinned_2 I;
#endif
#ifdef	SKIN_3
	v_model_skinned_3 I;
#endif
#ifdef	SKIN_4
	v_model_skinned_4 I;
	I.ind = v_model_ind;
#endif

	I.P = v_model_P;
	I.N = v_model_N;
	I.T = v_model_T;
	I.B = v_model_B;
	I.tc = v_model_tc;

	v2p_bumped O;
#ifdef 	SKIN_NONE
	O = _main(I);
#endif
#ifdef 	SKIN_0
	O = _main(skinning_0(I));
#endif
#ifdef	SKIN_1
	O = _main(skinning_1(I));
#endif
#ifdef	SKIN_2
	O = _main(skinning_2(I));
#endif
#ifdef	SKIN_3
	O = _main(skinning_3(I));
#endif
#ifdef	SKIN_4
	O = _main(skinning_4(I));
#endif

#if defined(USE_R2_STATIC_SUN) && !defined(USE_LM_HEMI)
	xrvary8		= O.tcdh;
#else
	xrvary8		= float4(O.tcdh, 0.0, 1.0);
#endif
	xrvary9	= O.position;
	xrvary10		= float4(O.M1, 1.0);
	xrvary11		= float4(O.M2, 1.0);
	xrvary12		= float4(O.M3, 1.0);
#ifdef USE_TDETAIL
	xrvary13	= float4(O.tcdbump, 0.0, 1.0);
#endif
#ifdef USE_LM_HEMI
	xrvary14		= float4(O.lmh, 0.0, 1.0);
#endif
	gl_Position			= O.hpos;
}
