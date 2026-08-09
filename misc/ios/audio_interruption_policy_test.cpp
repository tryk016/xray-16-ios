#include "src/xrSound/SoundInterruptionRegistry.h"

#include <cstdlib>
#include <fstream>
#include <iostream>
#include <memory>
#include <new>
#include <string>
#include <vector>

namespace
{
void require(const bool condition, const char* const message)
{
    if (!condition)
    {
        std::cerr << "FAIL: " << message << '\n';
        std::exit(1);
    }
}

struct FakeScene
{
    explicit FakeScene(const int initialPauseCount = 0) : pauseCount(initialPauseCount) {}

    int pauseCount = 0;
    int pauses = 0;
    int resumes = 0;

    void pause_emitters(const bool pause)
    {
        if (pause)
        {
            ++pauseCount;
            ++pauses;
        }
        else
        {
            --pauseCount;
            ++resumes;
        }
    }
};

class FakeSoundManager
{
public:
    void create(FakeScene* const scene) { m_scenes.push_back(scene); }

    void destroy(FakeScene* const scene)
    {
        xr_sound::interruption::Forget(m_interruptionScenes, scene);
        m_scenes.erase(std::remove(m_scenes.begin(), m_scenes.end(), scene), m_scenes.end());
    }

    void begin_audio_interruption()
    {
        xr_sound::interruption::Begin(m_interruptionActive, m_interruptionScenes, m_scenes,
            [](FakeScene* const scene) { scene->pause_emitters(true); });
    }

    void end_audio_interruption()
    {
        xr_sound::interruption::End(m_interruptionActive, m_interruptionScenes,
            [](FakeScene* const scene) { scene->pause_emitters(false); });
    }

private:
    bool m_interruptionActive = false;
    std::vector<FakeScene*> m_interruptionScenes;
    std::vector<FakeScene*> m_scenes;
};

void testNoSceneAtBeginDoesNotResumeNewScene()
{
    FakeSoundManager sound;
    FakeScene sceneB;

    sound.begin_audio_interruption();
    sound.create(&sceneB);
    sound.end_audio_interruption();

    require(sceneB.pauseCount == 0 && sceneB.pauses == 0 && sceneB.resumes == 0,
        "a scene created after an empty begin must not be resumed");
}

void testDestroyedSceneCannotResumeReplacementAtSameAddress()
{
    FakeSoundManager sound;
    alignas(FakeScene) unsigned char storage[sizeof(FakeScene)];
    auto* const sceneA = new (storage) FakeScene;
    sound.create(sceneA);
    sound.begin_audio_interruption();
    sound.destroy(sceneA);
    sceneA->~FakeScene();

    auto* const sceneB = new (storage) FakeScene;
    sound.create(sceneB);
    sound.end_audio_interruption();

    require(sceneB->pauseCount == 0 && sceneB->pauses == 0 && sceneB->resumes == 0,
        "replacement at a destroyed scene address must not receive a resume");
    sceneB->~FakeScene();
}

void testSurvivingScenePairsExactlyOnce()
{
    FakeSoundManager sound;
    FakeScene sceneA;
    sound.create(&sceneA);

    sound.begin_audio_interruption();
    sound.end_audio_interruption();

    require(sceneA.pauseCount == 0 && sceneA.pauses == 1 && sceneA.resumes == 1,
        "a surviving scene must receive exactly one pause and one resume");
}

void testExistingPauseCountIsPreserved()
{
    FakeSoundManager sound;
    FakeScene sceneA(3);
    sound.create(&sceneA);

    sound.begin_audio_interruption();
    sound.end_audio_interruption();

    require(sceneA.pauseCount == 3 && sceneA.pauses == 1 && sceneA.resumes == 1,
        "an interruption must restore a scene's pre-existing pause count");
}

void testDuplicateBeginEndAreIdempotent()
{
    FakeSoundManager sound;
    FakeScene sceneA;
    FakeScene sceneB;
    sound.create(&sceneA);

    sound.begin_audio_interruption();
    sound.create(&sceneB);
    sound.begin_audio_interruption();
    sound.end_audio_interruption();
    sound.end_audio_interruption();

    require(sceneA.pauseCount == 0 && sceneA.pauses == 1 && sceneA.resumes == 1,
        "duplicate begin/end must not duplicate a recorded scene transition");
    require(sceneB.pauseCount == 0 && sceneB.pauses == 0 && sceneB.resumes == 0,
        "a scene created between duplicate begins must not join the snapshot");
}

void testProductionCallbacksUseOnlyInterruptionApi()
{
    std::ifstream source("src/xrEngine/x_ray.cpp");
    require(source.good(), "must read x_ray.cpp from the repository root");
    const std::string contents((std::istreambuf_iterator<char>(source)), std::istreambuf_iterator<char>());
    require(contents.find("GEnv.Sound->begin_audio_interruption();") != std::string::npos,
        "iOS suspend callback must use the sound interruption begin API");
    require(contents.find("GEnv.Sound->end_audio_interruption();") != std::string::npos,
        "iOS resume callback must use the sound interruption end API");
    require(contents.find("GEnv.Sound->pause_emitters(") == std::string::npos,
        "iOS callbacks must not use the legacy global pause API");
}
} // namespace

int main()
{
    testNoSceneAtBeginDoesNotResumeNewScene();
    testDestroyedSceneCannotResumeReplacementAtSameAddress();
    testSurvivingScenePairsExactlyOnce();
    testExistingPauseCountIsPreserved();
    testDuplicateBeginEndAreIdempotent();
    testProductionCallbacksUseOnlyInterruptionApi();
    std::cout << "iOS audio interruption policy: PASS\n";
    return 0;
}
