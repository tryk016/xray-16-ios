#ifndef FS_internalH
#define FS_internalH
#pragma once

#include "lzhuf.h"
#if defined(XR_PLATFORM_APPLE_IOS)
#include "ios_private_save_writer.h"
#endif
#if defined(XR_PLATFORM_WINDOWS)
#include <io.h>
#endif
#include <fcntl.h>
#if defined(XR_PLATFORM_WINDOWS)
#include <sys\stat.h>
#include <share.h>
#endif

#if defined(XR_PLATFORM_BSD)
#define _sys_errlist sys_errlist
#endif

void* FileDownload(pcstr fn, size_t* pdwSize = nullptr);
void FileCompress(pcstr fn, pcstr sign, void* data, size_t size);
void* FileDecompress(pcstr fn, pcstr sign, size_t* size = nullptr);

class CFileWriter final : public IWriter
{
private:
    FILE* hf;
#if defined(XR_PLATFORM_APPLE_IOS)
    bool m_private_save = false;
    bool m_private_save_finalized = false;
    bool m_private_save_result = false;
#endif

public:
    CFileWriter(const char* name, bool exclusive, bool private_save = false)
    {
        R_ASSERT(name && name[0]);
        fName = name;
        VerifyPath(fName.c_str());
        pstr conv_fn = xr_strdup(name);
        convert_path_separators(conv_fn);
#if defined(XR_PLATFORM_APPLE_IOS)
        m_private_save = private_save;
#endif
        if (exclusive)
        {
            const int handle = _sopen(conv_fn, _O_WRONLY | _O_TRUNC | _O_CREAT | _O_BINARY, SH_DENYWR);
#ifndef MASTER_GOLD
            if (handle == -1)
            {
                string1024 error;
                xr_strerror(errno, error, sizeof(error));
                Msg("! Can't create file: '%s'. Error: '%s'.", conv_fn, error);
            }
#endif
            hf = _fdopen(handle, "wb");
        }
        else if (private_save)
        {
#if defined(XR_PLATFORM_APPLE_IOS)
            hf = ios_private_save_writer::open_for_rewrite(conv_fn);
#else
            R_ASSERT2(false, "Private save writer is iOS-only");
            hf = nullptr;
#endif
            if (hf == nullptr)
            {
                string1024 error;
                xr_strerror(errno, error, sizeof(error));
                Msg("! Can't securely write save file: '%s'. Error: '%s'.", conv_fn, error);
            }
        }
        else
        {
            hf = fopen(conv_fn, "wb");
            if (hf == 0)
            {
                string1024 error;
                xr_strerror(errno, error, sizeof(error));
                Msg("! Can't write file: '%s'. Error: '%s'.", conv_fn, error);
            }
        }
        xr_free(conv_fn);
    }

    ~CFileWriter() override
    {
        if (0 != hf)
        {
#if defined(XR_PLATFORM_APPLE_IOS)
            if (m_private_save)
            {
                if (!close_private_save())
                    Msg("! Can't securely finalize save file: '%s'.", fName.c_str());
            }
            else
#endif
            fclose(hf);
            // release RO attrib
#if defined(XR_PLATFORM_WINDOWS)
            u32 dwAttr = GetFileAttributes(fName.c_str());
            if ((dwAttr != u32(-1)) && (dwAttr & FILE_ATTRIBUTE_READONLY))
            {
                dwAttr &= ~FILE_ATTRIBUTE_READONLY;
                SetFileAttributes(fName.c_str(), dwAttr);
            }
#endif
        }
    }
    // kernel
    void w(const void* _ptr, size_t count) override
    {
        if ((0 != hf) && (0 != count))
        {
            const size_t mb_sz = 0x1000000; // 2 MB
            u8* ptr = (u8*)_ptr;
            size_t req_size;
            for (req_size = count; req_size > mb_sz; req_size -= mb_sz, ptr += mb_sz)
            {
                size_t W = fwrite(ptr, mb_sz, 1, hf);
                string1024 error;
                xr_strerror(errno, error, sizeof(error));
                R_ASSERT3(W == 1, "Can't write mem block to file. Disk maybe full.", error);
            }
            if (req_size)
            {
                size_t W = fwrite(ptr, req_size, 1, hf);
                string1024 error;
                xr_strerror(errno, error, sizeof(error));
                R_ASSERT3(W == 1, "Can't write mem block to file. Disk maybe full.", error);
            }
        }
    };

    void seek(size_t pos) override
    {
        if (0 != hf)
            fseek(hf, pos, SEEK_SET);
    };
    size_t tell() override { return (0 != hf) ? ftell(hf) : 0; };
    bool valid() override { return (0 != hf); }

#if defined(XR_PLATFORM_APPLE_IOS)
    bool close_private_save()
    {
        R_ASSERT2(m_private_save, "Private finalization requires a private iOS save writer");
        if (m_private_save_finalized)
            return m_private_save_result;

        m_private_save_finalized = true;
        m_private_save_result = ios_private_save_writer::finalize(hf);
        return m_private_save_result;
    }
#endif

    void flush() override
    {
        if (hf)
            fflush(hf);
    };
};

// It automatically frees memory after destruction
class CTempReader final : public IReader
{
public:
    CTempReader(void* _data, size_t _size, size_t _iterpos) : IReader(_data, _size, _iterpos) {}
    ~CTempReader() override;
};
class CPackReader final : public IReader
{
    void* base_address;

public:
    CPackReader(void* _base, void* _data, size_t _size) : IReader(_data, _size), base_address(_base) {}
    ~CPackReader() override;
};
class XRCORE_API CFileReader final : public IReader
{
public:
    CFileReader(pcstr name);
    ~CFileReader() override;
};
class CCompressedReader final : public IReader
{
public:
    CCompressedReader(const char* name, const char* sign);
    ~CCompressedReader() override;
};
class CVirtualFileReader final : public IReader
{
private:
#if defined(XR_PLATFORM_WINDOWS)
    void *hSrcFile, *hSrcMap;
#elif defined(XR_PLATFORM_POSIX)
    int hSrcFile;
#else
#   error Select or add implementation for your platform
#endif

public:
    CVirtualFileReader(pcstr cFileName);
    ~CVirtualFileReader() override;
};

#endif
