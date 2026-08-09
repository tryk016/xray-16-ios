#pragma once

#include <algorithm>

// The audio-interruption owner must resume exactly the scenes it paused.  Keep
// the bookkeeping independent of the sound engine so the same algorithm can be
// exercised by a strict host test without Objective-C or OpenAL dependencies.
namespace xr_sound::interruption
{
template <typename Registry, typename SceneRange, typename Pause>
void Begin(bool& active, Registry& pausedScenes, const SceneRange& scenes, Pause&& pause)
{
    if (active)
        return;

    // An empty snapshot is still an active interruption.  A scene created
    // later must not receive a synthetic counted resume at interruption end.
    active = true;
    pausedScenes.clear();
    pausedScenes.reserve(scenes.size());
    for (auto* const scene : scenes)
    {
        pause(scene);
        pausedScenes.push_back(scene);
    }
}

template <typename Registry, typename Scene>
void Forget(Registry& pausedScenes, Scene* const scene)
{
    pausedScenes.erase(std::remove(pausedScenes.begin(), pausedScenes.end(), scene), pausedScenes.end());
}

template <typename Registry, typename Resume>
void End(bool& active, Registry& pausedScenes, Resume&& resume)
{
    if (!active)
        return;

    for (auto* const scene : pausedScenes)
        resume(scene);

    pausedScenes.clear();
    active = false;
}
} // namespace xr_sound::interruption
