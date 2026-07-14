#include "common.h"

#ifndef GL_ES
out gl_PerVertex { vec4 gl_Position; };
#endif

layout(location = POSITION) in float4 P;

void main ()
{
	gl_Position = mul ( m_WVP, P );
}
