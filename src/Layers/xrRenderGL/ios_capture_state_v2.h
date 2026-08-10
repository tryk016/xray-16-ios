#pragma once

// Canonical, dependency-free representation of an iOS diagnostic frame
// capture.  Keeping serialization here lets a strict host unit test exercise
// the exact JSON and PPM token emitted by CHW::Present without a GL context.

#include <array>
#include <charconv>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <limits>
#include <string>

namespace xray::ios_capture_v2
{
inline constexpr std::uint32_t kPeriodMs = 5000;
inline constexpr std::size_t kSessionChars = 32;
inline constexpr std::size_t kUuidChars = 36;

enum class InputState : std::uint8_t
{
    None,
    Active,
    Released,
    Cancelled,
};

struct TimedEvent
{
    bool present{};
    std::uint32_t frame{};
    std::uint32_t continual_ms{};
    std::uint32_t sdl_ms{};
};

struct Capture
{
    char session[kSessionChars + 1]{};
    std::uint64_t sequence{};
    std::uint32_t pid{};
    std::uint32_t frame{};
    std::uint32_t continual_ms{};
    std::uint32_t width{};
    std::uint32_t height{};
    std::uint32_t period_ms{ kPeriodMs };
    char scene[16]{};
    bool paused{};
};

struct View
{
    std::array<float, 3> position{};
    std::array<float, 3> direction{};
    float fov{};
};

struct World
{
    bool present{};
    char level[128]{};
    std::uint64_t epoch{};
    std::uint64_t sector{};
};

struct Environment
{
    bool present{};
    std::uint64_t game_time_ms{};
    float day_time_s{};
    float time_factor{};
    char cycle[128]{};
    char weather[128]{};
    bool weather_fx{};
    char descriptor0[128]{};
    char descriptor1[128]{};
    float weight{};
    std::array<float, 3> ambient{};
    std::array<float, 4> hemi{};
    std::array<float, 3> sun{};
    std::array<float, 3> sun_direction{};
};

struct Input
{
    std::uint64_t generation{};
    InputState state{ InputState::None };
    char request_id[kUuidChars + 1]{};
    char key[32]{};
    int scancode{ -1 };
    std::uint32_t duration_ms{};
    TimedEvent accepted{};
    TimedEvent released{};
};

struct Snapshot
{
    Capture capture{};
    View view{};
    World world{};
    Environment environment{};
    Input input{};
};

inline bool IsLowerHex32(const char* value)
{
    if (!value || std::strlen(value) != kSessionChars)
        return false;
    for (std::size_t index = 0; index != kSessionChars; ++index)
    {
        const char c = value[index];
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')))
            return false;
    }
    return true;
}

inline bool IsCanonicalUuid(const char* value)
{
    if (!value || std::strlen(value) != kUuidChars)
        return false;
    for (std::size_t index = 0; index != kUuidChars; ++index)
    {
        if (index == 8 || index == 13 || index == 18 || index == 23)
        {
            if (value[index] != '-')
                return false;
            continue;
        }
        const char c = value[index];
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')))
            return false;
    }
    return true;
}

inline bool IsFinite(const float value) { return std::isfinite(value); }

template <std::size_t N>
inline bool IsFinite(const std::array<float, N>& values)
{
    for (const float value : values)
        if (!IsFinite(value))
            return false;
    return true;
}

inline bool IsSafeText(const char* value)
{
    if (!value || !value[0])
        return false;
    for (const unsigned char* cursor = reinterpret_cast<const unsigned char*>(value); *cursor; ++cursor)
        if (*cursor < 0x20)
            return false;
    return true;
}

inline const char* InputStateName(const InputState state)
{
    switch (state)
    {
    case InputState::None: return "none";
    case InputState::Active: return "active";
    case InputState::Released: return "released";
    case InputState::Cancelled: return "cancelled";
    }
    return nullptr;
}

inline bool IsKnownScene(const char* scene)
{
    return scene && (std::strcmp(scene, "gameplay") == 0 || std::strcmp(scene, "menu") == 0
        || std::strcmp(scene, "loading") == 0);
}

inline bool IsInputShapeValid(const Input& input, std::string& error)
{
    if (!InputStateName(input.state))
    {
        error = "input state is invalid";
        return false;
    }

    if (input.state == InputState::None)
    {
        if (input.generation != 0 || input.request_id[0] || input.key[0] || input.scancode != -1
            || input.duration_ms != 0 || input.accepted.present || input.released.present)
        {
            error = "none input carries an event";
            return false;
        }
        return true;
    }

    if (input.generation == 0 || !IsCanonicalUuid(input.request_id) || !IsSafeText(input.key)
        || input.scancode < -1 || input.scancode > 512 || input.duration_ms > 30000)
    {
        error = "input identity is invalid";
        return false;
    }
    if (input.state == InputState::Active && input.released.present)
    {
        error = "active input already has a release event";
        return false;
    }
    if (input.state == InputState::Released)
    {
        if (!input.accepted.present || !input.released.present || input.released.frame < input.accepted.frame
            || input.released.continual_ms < input.accepted.continual_ms
            || input.released.sdl_ms < input.accepted.sdl_ms)
        {
            error = "released input has an invalid release event";
            return false;
        }
    }
    if (input.state == InputState::Cancelled && input.released.present)
    {
        error = "cancelled input must not claim a release event";
        return false;
    }
    return true;
}

inline bool IsValid(const Snapshot& value, std::string& error)
{
    const Capture& capture = value.capture;
    if (!IsLowerHex32(capture.session) || capture.sequence == 0 || capture.pid == 0 || capture.width == 0
        || capture.height == 0 || capture.width > 16384 || capture.height > 16384 || capture.period_ms != kPeriodMs
        || !IsKnownScene(capture.scene))
    {
        error = "capture fields are invalid";
        return false;
    }
    if (!IsFinite(value.view.position) || !IsFinite(value.view.direction) || !IsFinite(value.view.fov)
        || value.view.fov <= 0.f || value.view.fov > 180.f)
    {
        error = "view fields are invalid";
        return false;
    }
    const bool gameplay = std::strcmp(capture.scene, "gameplay") == 0;
    if ((value.world.present && !gameplay) || (gameplay && !value.world.present)
        || (value.environment.present && !value.world.present))
    {
        error = "scene/world/environment relationship is invalid";
        return false;
    }
    if (value.world.present && (!IsSafeText(value.world.level) || value.world.epoch == 0))
    {
        error = "world fields are invalid";
        return false;
    }
    const Environment& environment = value.environment;
    if (environment.present)
    {
        if (!IsFinite(environment.day_time_s) || !IsFinite(environment.time_factor) || !IsFinite(environment.weight)
            || !IsFinite(environment.ambient) || !IsFinite(environment.hemi) || !IsFinite(environment.sun)
            || !IsFinite(environment.sun_direction) || !IsSafeText(environment.cycle) || !IsSafeText(environment.weather)
            || !IsSafeText(environment.descriptor0) || !IsSafeText(environment.descriptor1)
            || environment.day_time_s < 0.f || environment.day_time_s > 86400.f || environment.time_factor <= 0.f
            || environment.time_factor > 10000.f || environment.weight < 0.f || environment.weight > 1.f)
        {
            error = "environment fields are invalid";
            return false;
        }
    }
    const auto eventAfterCapture = [&capture](const TimedEvent& event) {
        return event.present && (event.frame > capture.frame || event.continual_ms > capture.continual_ms);
    };
    if (eventAfterCapture(value.input.accepted) || eventAfterCapture(value.input.released))
    {
        error = "input event is newer than its capture";
        return false;
    }
    return IsInputShapeValid(value.input, error);
}

inline void AppendEscaped(std::string& output, const char* value)
{
    output.push_back('"');
    for (const unsigned char* cursor = reinterpret_cast<const unsigned char*>(value); *cursor; ++cursor)
    {
        switch (*cursor)
        {
        case '"': output += "\\\""; break;
        case '\\': output += "\\\\"; break;
        case '\b': output += "\\b"; break;
        case '\f': output += "\\f"; break;
        case '\n': output += "\\n"; break;
        case '\r': output += "\\r"; break;
        case '\t': output += "\\t"; break;
        default:
            if (*cursor < 0x20)
            {
                static constexpr char hex[] = "0123456789abcdef";
                output += "\\u00";
                output.push_back(hex[(*cursor >> 4) & 0x0f]);
                output.push_back(hex[*cursor & 0x0f]);
            }
            else
                output.push_back(static_cast<char>(*cursor));
            break;
        }
    }
    output.push_back('"');
}

inline void AppendUnsigned(std::string& output, const std::uint64_t value) { output += std::to_string(value); }

inline bool AppendFloat(std::string& output, const float value)
{
    char buffer[64]{};
    // std::to_chars does not observe LC_NUMERIC, unlike snprintf. Capture JSON
    // must remain byte-stable while the game or a third-party library changes
    // the process locale.
    const auto converted = std::to_chars(buffer, buffer + sizeof(buffer), value == 0.f ? 0.f : value,
        std::chars_format::general, std::numeric_limits<float>::max_digits10);
    if (converted.ec != std::errc{})
        return false;
    output.append(buffer, converted.ptr);
    return true;
}

template <std::size_t N>
inline bool AppendFloatArray(std::string& output, const std::array<float, N>& values)
{
    output.push_back('[');
    for (std::size_t index = 0; index != N; ++index)
    {
        if (index)
            output.push_back(',');
        if (!AppendFloat(output, values[index]))
            return false;
    }
    output.push_back(']');
    return true;
}

inline void AppendTimedEvent(std::string& output, const TimedEvent& event)
{
    if (!event.present)
    {
        output += "null";
        return;
    }
    output += "{\"frame\":";
    AppendUnsigned(output, event.frame);
    output += ",\"continual_ms\":";
    AppendUnsigned(output, event.continual_ms);
    output += ",\"sdl_ms\":";
    AppendUnsigned(output, event.sdl_ms);
    output.push_back('}');
}

inline bool Serialize(const Snapshot& value, std::string& output, std::string& error)
{
    if (!IsValid(value, error))
        return false;

    const Capture& capture = value.capture;
    output.clear();
    output.reserve(1600);
    output += "{\"schema\":\"openxray.capture.v2\",\"capture\":{\"token\":";
    const std::string token = std::string(capture.session) + ":" + std::to_string(capture.sequence);
    AppendEscaped(output, token.c_str());
    output += ",\"session\":";
    AppendEscaped(output, capture.session);
    output += ",\"sequence\":";
    AppendUnsigned(output, capture.sequence);
    output += ",\"pid\":";
    AppendUnsigned(output, capture.pid);
    output += ",\"frame\":";
    AppendUnsigned(output, capture.frame);
    output += ",\"continual_ms\":";
    AppendUnsigned(output, capture.continual_ms);
    output += ",\"width\":";
    AppendUnsigned(output, capture.width);
    output += ",\"height\":";
    AppendUnsigned(output, capture.height);
    output += ",\"period_ms\":";
    AppendUnsigned(output, capture.period_ms);
    output += ",\"scene\":";
    AppendEscaped(output, capture.scene);
    output += ",\"paused\":";
    output += capture.paused ? "true" : "false";
    output += "},\"view\":{\"position\":";
    if (!AppendFloatArray(output, value.view.position))
    {
        output.clear();
        error = "could not format finite floating-point value";
        return false;
    }
    output += ",\"direction\":";
    if (!AppendFloatArray(output, value.view.direction))
    {
        output.clear();
        error = "could not format finite floating-point value";
        return false;
    }
    output += ",\"fov\":";
    if (!AppendFloat(output, value.view.fov))
    {
        output.clear();
        error = "could not format finite floating-point value";
        return false;
    }
    output += "},\"world\":";
    if (!value.world.present)
        output += "null";
    else
    {
        output += "{\"level\":";
        AppendEscaped(output, value.world.level);
        output += ",\"epoch\":";
        AppendUnsigned(output, value.world.epoch);
        output += ",\"sector\":";
        AppendUnsigned(output, value.world.sector);
        output.push_back('}');
    }
    output += ",\"environment\":";
    if (!value.environment.present)
        output += "null";
    else
    {
        const Environment& environment = value.environment;
        output += "{\"game_time_ms\":";
        AppendUnsigned(output, environment.game_time_ms);
        output += ",\"day_time_s\":";
        if (!AppendFloat(output, environment.day_time_s))
        {
            output.clear();
            error = "could not format finite floating-point value";
            return false;
        }
        output += ",\"time_factor\":";
        if (!AppendFloat(output, environment.time_factor))
        {
            output.clear();
            error = "could not format finite floating-point value";
            return false;
        }
        output += ",\"cycle\":";
        AppendEscaped(output, environment.cycle);
        output += ",\"weather\":";
        AppendEscaped(output, environment.weather);
        output += ",\"weather_fx\":";
        output += environment.weather_fx ? "true" : "false";
        output += ",\"descriptor0\":";
        AppendEscaped(output, environment.descriptor0);
        output += ",\"descriptor1\":";
        AppendEscaped(output, environment.descriptor1);
        output += ",\"weight\":";
        if (!AppendFloat(output, environment.weight))
        {
            output.clear();
            error = "could not format finite floating-point value";
            return false;
        }
        output += ",\"ambient\":";
        if (!AppendFloatArray(output, environment.ambient))
        {
            output.clear();
            error = "could not format finite floating-point value";
            return false;
        }
        output += ",\"hemi\":";
        if (!AppendFloatArray(output, environment.hemi))
        {
            output.clear();
            error = "could not format finite floating-point value";
            return false;
        }
        output += ",\"sun\":";
        if (!AppendFloatArray(output, environment.sun))
        {
            output.clear();
            error = "could not format finite floating-point value";
            return false;
        }
        output += ",\"sun_direction\":";
        if (!AppendFloatArray(output, environment.sun_direction))
        {
            output.clear();
            error = "could not format finite floating-point value";
            return false;
        }
        output.push_back('}');
    }
    output += ",\"input\":{\"generation\":";
    AppendUnsigned(output, value.input.generation);
    output += ",\"state\":";
    AppendEscaped(output, InputStateName(value.input.state));
    output += ",\"request_id\":";
    if (value.input.state == InputState::None)
        output += "null";
    else
        AppendEscaped(output, value.input.request_id);
    output += ",\"key\":";
    if (value.input.state == InputState::None)
        output += "null";
    else
        AppendEscaped(output, value.input.key);
    output += ",\"scancode\":";
    if (value.input.state == InputState::None)
        output += "null";
    else
        output += std::to_string(value.input.scancode);
    output += ",\"duration_ms\":";
    AppendUnsigned(output, value.input.duration_ms);
    output += ",\"accepted\":";
    AppendTimedEvent(output, value.input.accepted);
    output += ",\"released\":";
    AppendTimedEvent(output, value.input.released);
    output += "}}";
    return true;
}

inline std::string PpmHeader(const Snapshot& value)
{
    return "P6\n# openxray-capture-v2 token=" + std::string(value.capture.session) + ":"
        + std::to_string(value.capture.sequence) + "\n" + std::to_string(value.capture.width) + " "
        + std::to_string(value.capture.height) + "\n255\n";
}
} // namespace xray::ios_capture_v2
