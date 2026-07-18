#include "common.h"
#ifdef GL_ES
// Vertex-stage texelFetch of s_tonemap (VTF) returns 0 on the ES monolithic
// path (vertex-sampler binding) — the sky/clouds pre-scaled to black. Use the
// non-VTF path: tonemap sampled in the fragment stage, like the combine pass.
#undef USE_VTF
#endif
#include "shared\cloudconfig.h"
#include "iostructs\v_clouds.h"

vf _main (vi v)
{
	vf 		o;

	o.hpos 		= mul		(m_WVP, v.p);	// xform, input in world coords
	
//	if (length(float3(v.p.x,0,v.p.z))>CLOUD_FADE)	o.color.w = 0	;

	// generate tcs
	float2  d0	= v.dir.xy*2.0-1.0;
	float2  d1	= v.dir.wz*2.0-1.0;
	float2 	_0	= v.p.xz * CLOUD_TILE0 + d0*timers.z*CLOUD_SPEED0;
	float2 	_1	= v.p.xz * CLOUD_TILE1 + d1*timers.z*CLOUD_SPEED1;
	o.tc0		= _0;					// copy tc
	o.tc1		= _1;					// copy tc

	o.color		=	v.color	;			// copy color, low precision, cannot prescale even by 2
	o.color.w	*= 	pow		(v.p.y,25.0);

#ifdef USE_VTF
	float scale	=	texelFetch(s_tonemap, int2(0,0), 0).x;
	o.color.rgb	*=	scale;		// high precision
#endif // USE_VTF

	return o;
}
