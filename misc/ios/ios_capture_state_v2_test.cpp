#include "src/Layers/xrRenderGL/ios_capture_state_v2.h"

#include <cmath>
#include <clocale>
#include <cstdio>
#include <cstring>
#include <limits>
#include <string>

namespace
{
int failures{};

void check(const bool condition, const char* message)
{
    if (!condition)
    {
        std::fprintf(stderr, "FAIL: %s\n", message);
        ++failures;
    }
}

template <std::size_t Size>
void copy(char (&destination)[Size], const char* source)
{
    std::snprintf(destination, Size, "%s", source);
}

xray::ios_capture_v2::Snapshot valid_snapshot()
{
    using namespace xray::ios_capture_v2;
    Snapshot result{};
    copy(result.capture.session, "0123456789abcdef0123456789abcdef");
    result.capture.sequence = 7;
    result.capture.pid = 42;
    result.capture.frame = 200;
    result.capture.continual_ms = 20000;
    result.capture.width = 1864;
    result.capture.height = 860;
    result.capture.period_ms = kPeriodMs;
    copy(result.capture.scene, "gameplay");
    result.view.position = { 1.f, 2.f, 3.f };
    result.view.direction = { 0.f, 0.f, 1.f };
    result.view.fov = 67.5f;
    result.world.present = true;
    copy(result.world.level, "zaton");
    result.world.epoch = 2;
    result.world.sector = 115;
    result.environment.present = true;
    result.environment.game_time_ms = 123456;
    result.environment.day_time_s = 43200.f;
    result.environment.time_factor = 10.f;
    copy(result.environment.cycle, "default");
    copy(result.environment.weather, "default");
    copy(result.environment.descriptor0, "12:00:00");
    copy(result.environment.descriptor1, "13:00:00");
    result.environment.weight = 0.25f;
    result.environment.ambient = { .1f, .2f, .3f };
    result.environment.hemi = { .4f, .5f, .6f, .7f };
    result.environment.sun = { .8f, .9f, 1.f };
    result.environment.sun_direction = { 0.f, -1.f, 0.f };
    result.input.generation = 1;
    result.input.state = InputState::Released;
    copy(result.input.request_id, "12345678-1234-4abc-8def-1234567890ab");
    copy(result.input.key, "w");
    result.input.scancode = 26;
    result.input.duration_ms = 12000;
    result.input.accepted = { true, 101, 5100, 6100 };
    result.input.released = { true, 102, 17100, 18100 };
    return result;
}
} // namespace

int main()
{
    using namespace xray::ios_capture_v2;
    Snapshot value = valid_snapshot();
    std::string json;
    std::string error;
    check(Serialize(value, json, error), "valid snapshot serializes");
    check(json.find("{\"schema\":\"openxray.capture.v2\"") == 0, "schema is canonical first field");
    check(json.find("\"token\":\"0123456789abcdef0123456789abcdef:7\"") != std::string::npos,
        "token is canonical session plus sequence");
    check(json.find("\"game_time_ms\":123456") != std::string::npos, "absolute game_time_ms is preserved");
    check(PpmHeader(value) == "P6\n# openxray-capture-v2 token=0123456789abcdef0123456789abcdef:7\n1864 860\n255\n",
        "PPM header binds the canonical token");

    const char* nonC = nullptr;
    for (const char* candidate : { "pl_PL.UTF-8", "de_DE.UTF-8", "fr_FR.UTF-8" })
    {
        if (std::setlocale(LC_NUMERIC, candidate))
        {
            nonC = candidate;
            break;
        }
    }
    check(nonC != nullptr, "a non-C numeric locale must be available for locale-independent formatting coverage");
    if (nonC)
    {
        check(Serialize(value, json, error), "valid snapshot serializes under a non-C locale");
        check(json.find("67.5") != std::string::npos && json.find("67,5") == std::string::npos,
            "JSON float formatting does not follow LC_NUMERIC");
    }
    std::setlocale(LC_NUMERIC, "C");

    value.capture.session[0] = 'A';
    check(!Serialize(value, json, error), "uppercase session is rejected");
    value = valid_snapshot();
    value.view.fov = std::numeric_limits<float>::quiet_NaN();
    check(!Serialize(value, json, error), "NaN view scalar is rejected");
    value = valid_snapshot();
    value.world.epoch = 0;
    check(!Serialize(value, json, error), "world without epoch is rejected");
    value = valid_snapshot();
    copy(value.capture.scene, "loading");
    check(!Serialize(value, json, error), "loading capture cannot claim a gameplay world");
    value = valid_snapshot();
    value.world.present = false;
    check(!Serialize(value, json, error), "gameplay capture must carry a world");
    value = valid_snapshot();
    value.input.released.frame = value.capture.frame + 1;
    check(!Serialize(value, json, error), "input event newer than capture is rejected");
    value = valid_snapshot();
    value.environment.weight = 1.01f;
    check(!Serialize(value, json, error), "out-of-range environment weight is rejected");
    value = valid_snapshot();
    value.input.request_id[0] = 'A';
    check(!Serialize(value, json, error), "noncanonical input UUID is rejected");
    value = valid_snapshot();
    value.input.released.continual_ms = value.input.accepted.continual_ms - 1;
    check(!Serialize(value, json, error), "release before acceptance is rejected");
    value = valid_snapshot();
    value.input.state = InputState::None;
    check(!Serialize(value, json, error), "none input with stale fields is rejected");
    value = valid_snapshot();
    value.input.state = InputState::Active;
    value.input.accepted = {};
    value.input.released = {};
    check(Serialize(value, json, error), "pending active input is representable");
    value.input.state = InputState::Cancelled;
    check(Serialize(value, json, error), "pre-dispatch cancelled input is representable");

    return failures == 0 ? 0 : 1;
}
