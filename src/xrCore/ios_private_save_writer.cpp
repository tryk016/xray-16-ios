#include "ios_private_save_writer.h"

#if defined(XR_PLATFORM_APPLE_IOS)
#include <cerrno>
#include <cstdio>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

namespace
{
constexpr mode_t private_save_mode = S_IRUSR | S_IWUSR;

void close_with_errno(const int descriptor, const int error)
{
    close(descriptor);
    errno = error;
}

bool is_safe_existing_save(const struct stat& status)
{
    return S_ISREG(status.st_mode) && status.st_uid == getuid() && status.st_nlink == 1;
}

int system_flush(FILE* stream)
{
    return fflush(stream);
}

int system_close(FILE* stream)
{
    return fclose(stream);
}

#if defined(XR_IOS_PRIVATE_SAVE_WRITER_TESTING)
const ios_private_save_writer::FinalizeOperations system_finalize_operations = {system_flush, system_close};
const ios_private_save_writer::FinalizeOperations* finalize_operations = &system_finalize_operations;
#endif

int finalize_flush(FILE* stream)
{
#if defined(XR_IOS_PRIVATE_SAVE_WRITER_TESTING)
    return finalize_operations->flush(stream);
#else
    return system_flush(stream);
#endif
}

int finalize_close(FILE* stream)
{
#if defined(XR_IOS_PRIVATE_SAVE_WRITER_TESTING)
    return finalize_operations->close(stream);
#else
    return system_close(stream);
#endif
}

int saved_errno_or_io_error(const int error)
{
    return error != 0 ? error : EIO;
}
}

namespace ios_private_save_writer
{
FILE* open_for_rewrite(const char* path)
{
    const int common_flags = O_WRONLY | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC;
    int descriptor = open(path, common_flags);
    if (descriptor == -1 && errno == ENOENT)
        descriptor = open(path, common_flags | O_CREAT | O_EXCL, private_save_mode);
    if (descriptor == -1)
        return nullptr;

    struct stat status;
    if (fstat(descriptor, &status) != 0)
    {
        const int error = errno;
        close_with_errno(descriptor, error);
        return nullptr;
    }

    if (!is_safe_existing_save(status))
    {
        close_with_errno(descriptor, EPERM);
        return nullptr;
    }

    if (fchmod(descriptor, private_save_mode) != 0)
    {
        const int error = errno;
        close_with_errno(descriptor, error);
        return nullptr;
    }

    FILE* stream = fdopen(descriptor, "wb");
    if (stream == nullptr)
    {
        const int error = errno;
        close_with_errno(descriptor, error);
        return nullptr;
    }

    if (ftruncate(fileno(stream), 0) != 0)
    {
        const int error = errno;
        fclose(stream);
        errno = error;
        return nullptr;
    }

    return stream;
}

bool finalize(FILE*& stream)
{
    if (stream == nullptr)
    {
        errno = EINVAL;
        return false;
    }

    errno = 0;
    const int flush_result = finalize_flush(stream);
    const int flush_error = errno;
    errno = 0;
    const int close_result = finalize_close(stream);
    const int close_error = errno;
    stream = nullptr;

    if (flush_result != 0)
    {
        errno = saved_errno_or_io_error(flush_error);
        return false;
    }
    if (close_result != 0)
    {
        errno = saved_errno_or_io_error(close_error);
        return false;
    }
    return true;
}

#if defined(XR_IOS_PRIVATE_SAVE_WRITER_TESTING)
void set_finalize_operations_for_testing(const FinalizeOperations* operations)
{
    finalize_operations = operations ? operations : &system_finalize_operations;
}

void reset_finalize_operations_for_testing()
{
    finalize_operations = &system_finalize_operations;
}
#endif
}
#endif
