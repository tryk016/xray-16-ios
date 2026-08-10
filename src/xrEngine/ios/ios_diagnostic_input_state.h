#pragma once

// iOS-only diagnostic input is intentionally kept out of xr_input.h.  The
// renderer only needs an immutable POD record for a capture sidecar; exposing
// this small interface avoids making every CInput consumer rebuild when the
// diagnostic protocol changes.

#include <cstdint>

namespace ios_diagnostic_input
{
enum class State : std::uint8_t
{
    None,
    Active,
    Released,
    Cancelled,
};

struct Event
{
    bool present{};
    std::uint32_t frame{};
    std::uint32_t continual_ms{};
    std::uint32_t sdl_ms{};
};

struct Snapshot
{
    std::uint64_t generation{};
    State state{ State::None };
    char request_id[37]{}; // canonical lowercase UUID, plus NUL
    char key[32]{};
    int scancode{ -1 };
    std::uint32_t duration_ms{};
    Event accepted{};
    Event released{};
};

// A tap can be cancelled after its pointer move but before the delayed click.
// That is still a real diagnostic request; it must become Cancelled instead of
// being left Active merely because no dispatch acknowledgement exists yet.
inline bool transition_to_cancelled(Snapshot& value)
{
    if (value.state != State::Active)
        return false;
    value.released = {};
    value.state = State::Cancelled;
    return true;
}

// All calls are made by the engine's main thread. Snapshot() returns by value
// so Present() freezes one self-consistent input observation before readback.
Snapshot snapshot();
void reset();
void begin(const char* request_id, const char* key, int scancode, std::uint32_t duration_ms,
    std::uint32_t frame, std::uint32_t continual_ms, std::uint32_t sdl_ms);
void accept(std::uint32_t frame, std::uint32_t continual_ms, std::uint32_t sdl_ms);
void release(std::uint32_t frame, std::uint32_t continual_ms, std::uint32_t sdl_ms);
void cancel(std::uint32_t frame, std::uint32_t continual_ms, std::uint32_t sdl_ms);
} // namespace ios_diagnostic_input
