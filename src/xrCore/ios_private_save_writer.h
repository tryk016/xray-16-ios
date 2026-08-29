#pragma once

#if defined(XR_PLATFORM_APPLE_IOS)
#include <cstdio>

namespace ios_private_save_writer
{
FILE* open_for_rewrite(const char* path);
bool finalize(FILE*& stream);

#if defined(XR_IOS_PRIVATE_SAVE_WRITER_TESTING)
struct FinalizeOperations
{
    int (*flush)(FILE* stream);
    int (*close)(FILE* stream);
};

void set_finalize_operations_for_testing(const FinalizeOperations* operations);
void reset_finalize_operations_for_testing();
#endif
}
#endif
