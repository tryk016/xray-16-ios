#include "src/xrEngine/ios/ios_diagnostic_input_state.h"

#include <cstdio>
#include <cstring>

int main()
{
    ios_diagnostic_input::Snapshot value{};
    value.generation = 4;
    value.state = ios_diagnostic_input::State::Active;
    std::strcpy(value.request_id, "12345678-1234-4abc-8def-1234567890ab");
    std::strcpy(value.key, "tap");
    value.scancode = -1;
    // This models the delayed tap window: parser accepted the trigger and
    // moved the cursor, but the click/ack has not run yet.
    if (!ios_diagnostic_input::transition_to_cancelled(value))
        return 1;
    if (value.state != ios_diagnostic_input::State::Cancelled || value.accepted.present || value.released.present)
        return 2;
    if (ios_diagnostic_input::transition_to_cancelled(value))
        return 3;
    return 0;
}
