#pragma once

struct SDL_Window;

namespace ios_display
{
// The game keeps SDL window/input coordinates in UIKit points, but presents to
// a 2x OpenGL backing store. This gives the requested 1864x860 drawable on the
// current 932x430 landscape window without rendering to a separate supersample
// target and scaling it during Present().
inline constexpr float OpenGLDrawableScale = 2.0f;

bool set_opengl_drawable_scale(SDL_Window* window, float scale);
} // namespace ios_display
