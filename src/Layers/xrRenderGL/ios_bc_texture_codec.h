#pragma once

#include <cstddef>
#include <cstdint>
#include <limits>

// This is intentionally independent of GL and GLI.  It documents the current iOS
// compatibility fallback as a small, host-testable contract: decoded BC pixels are
// uploaded as linear RGBA8, even when the DDS source was tagged sRGB.  Choosing an
// sRGB GL storage format is a later visual-validation decision, not an incidental
// change to this decoder.
namespace xray::render::ios_bc
{
enum class Kind
{
    none,
    bc1,
    bc2,
    bc3,
    bc4,
    bc5,
};

enum class Transfer
{
    linear,
    srgb,
};

enum class Layout
{
    rgba,
    rrr1,
    rg01,
};

enum class Storage
{
    rgba8Unorm,
    srgb8Alpha8,
};

enum class SwizzlePolicy
{
    ignoreGliSource,
};

struct FormatInfo
{
    Kind kind = Kind::none;
    Transfer sourceTransfer = Transfer::linear;
    Layout decodedLayout = Layout::rgba;
};

struct UploadPlan
{
    Storage storage = Storage::rgba8Unorm;
    SwizzlePolicy swizzle = SwizzlePolicy::ignoreGliSource;
};

namespace detail
{
constexpr bool MultiplyWouldOverflow(const std::size_t left, const std::size_t right) noexcept
{
    return left != 0 && right > std::numeric_limits<std::size_t>::max() / left;
}

constexpr std::size_t BlockCount(const std::size_t extent) noexcept
{
    return extent / 4 + (extent % 4 == 0 ? 0 : 1);
}

struct Rgba
{
    std::uint8_t r;
    std::uint8_t g;
    std::uint8_t b;
    std::uint8_t a;
};

inline std::uint16_t ReadLe16(const std::uint8_t* const source) noexcept
{
    return static_cast<std::uint16_t>(source[0])
        | static_cast<std::uint16_t>(static_cast<std::uint16_t>(source[1]) << 8);
}

inline std::uint32_t ReadLe32(const std::uint8_t* const source) noexcept
{
    return static_cast<std::uint32_t>(source[0])
        | (static_cast<std::uint32_t>(source[1]) << 8)
        | (static_cast<std::uint32_t>(source[2]) << 16)
        | (static_cast<std::uint32_t>(source[3]) << 24);
}

inline Rgba Expand565(const std::uint16_t color) noexcept
{
    return {
        static_cast<std::uint8_t>(((color >> 11) & 31U) * 255U / 31U),
        static_cast<std::uint8_t>(((color >> 5) & 63U) * 255U / 63U),
        static_cast<std::uint8_t>((color & 31U) * 255U / 31U),
        255,
    };
}

inline void DecodeColorBlock(const std::uint8_t* const source, Rgba output[16], const bool bc1) noexcept
{
    const std::uint16_t color0 = ReadLe16(source);
    const std::uint16_t color1 = ReadLe16(source + 2);
    Rgba palette[4];
    palette[0] = Expand565(color0);
    palette[1] = Expand565(color1);

    if (!bc1 || color0 > color1)
    {
        palette[2] = {
            static_cast<std::uint8_t>((2U * palette[0].r + palette[1].r) / 3U),
            static_cast<std::uint8_t>((2U * palette[0].g + palette[1].g) / 3U),
            static_cast<std::uint8_t>((2U * palette[0].b + palette[1].b) / 3U),
            255,
        };
        palette[3] = {
            static_cast<std::uint8_t>((palette[0].r + 2U * palette[1].r) / 3U),
            static_cast<std::uint8_t>((palette[0].g + 2U * palette[1].g) / 3U),
            static_cast<std::uint8_t>((palette[0].b + 2U * palette[1].b) / 3U),
            255,
        };
    }
    else
    {
        palette[2] = {
            static_cast<std::uint8_t>((palette[0].r + palette[1].r) / 2U),
            static_cast<std::uint8_t>((palette[0].g + palette[1].g) / 2U),
            static_cast<std::uint8_t>((palette[0].b + palette[1].b) / 2U),
            255,
        };
        palette[3] = { 0, 0, 0, 0 };
    }

    const std::uint32_t selectors = ReadLe32(source + 4);
    for (std::size_t index = 0; index < 16; ++index)
        output[index] = palette[(selectors >> (2U * static_cast<unsigned>(index))) & 3U];
}

inline void DecodeAlphaBlock(const std::uint8_t* const source, std::uint8_t output[16]) noexcept
{
    const std::uint8_t alpha0 = source[0];
    const std::uint8_t alpha1 = source[1];
    std::uint8_t palette[8];
    palette[0] = alpha0;
    palette[1] = alpha1;

    if (alpha0 > alpha1)
    {
        for (unsigned index = 1; index < 7; ++index)
            palette[index + 1] = static_cast<std::uint8_t>(
                ((7U - index) * alpha0 + index * alpha1) / 7U);
    }
    else
    {
        for (unsigned index = 1; index < 5; ++index)
            palette[index + 1] = static_cast<std::uint8_t>(
                ((5U - index) * alpha0 + index * alpha1) / 5U);
        palette[6] = 0;
        palette[7] = 255;
    }

    std::uint64_t selectors = 0;
    for (unsigned index = 0; index < 6; ++index)
        selectors |= static_cast<std::uint64_t>(source[index + 2]) << (8U * index);
    for (std::size_t index = 0; index < 16; ++index)
        output[index] = palette[(selectors >> (3U * static_cast<unsigned>(index))) & 7U];
}

inline void StorePixel(std::uint8_t* const destination, const std::size_t width,
    const std::size_t x, const std::size_t y, const Rgba& color) noexcept
{
    const std::size_t offset = (y * width + x) * 4;
    destination[offset] = color.r;
    destination[offset + 1] = color.g;
    destination[offset + 2] = color.b;
    destination[offset + 3] = color.a;
}
} // namespace detail

constexpr std::size_t BlockBytes(const Kind kind) noexcept
{
    return kind == Kind::bc1 || kind == Kind::bc4 ? 8
        : kind == Kind::bc2 || kind == Kind::bc3 || kind == Kind::bc5 ? 16
                                                                     : 0;
}

constexpr std::size_t EncodedLevelBytes(const Kind kind, const std::size_t width,
    const std::size_t height) noexcept
{
    const std::size_t blockBytes = BlockBytes(kind);
    if (blockBytes == 0 || width == 0 || height == 0)
        return 0;

    const std::size_t blockWidth = detail::BlockCount(width);
    const std::size_t blockHeight = detail::BlockCount(height);
    if (detail::MultiplyWouldOverflow(blockWidth, blockHeight))
        return 0;
    const std::size_t blockCount = blockWidth * blockHeight;
    if (detail::MultiplyWouldOverflow(blockCount, blockBytes))
        return 0;
    return blockCount * blockBytes;
}

constexpr std::size_t DecodedLevelBytes(const std::size_t width, const std::size_t height) noexcept
{
    if (width == 0 || height == 0 || detail::MultiplyWouldOverflow(width, height))
        return 0;
    const std::size_t pixels = width * height;
    if (detail::MultiplyWouldOverflow(pixels, 4))
        return 0;
    return pixels * 4;
}

constexpr UploadPlan CurrentUploadPlan(const FormatInfo) noexcept
{
    // Preserve the deployed fallback exactly.  The source transfer is recorded in
    // FormatInfo for a later, frame-validated color-space decision.
    return { Storage::rgba8Unorm, SwizzlePolicy::ignoreGliSource };
}

inline bool DecodeLevel(const Kind kind, const std::uint8_t* const source,
    const std::size_t sourceBytes, const std::size_t width, const std::size_t height,
    std::uint8_t* const destination, const std::size_t destinationBytes) noexcept
{
    const std::size_t encodedBytes = EncodedLevelBytes(kind, width, height);
    const std::size_t decodedBytes = DecodedLevelBytes(width, height);
    if (source == nullptr || destination == nullptr || encodedBytes == 0 || decodedBytes == 0
        || sourceBytes < encodedBytes || destinationBytes < decodedBytes)
        return false;

    const std::size_t blockWidth = detail::BlockCount(width);
    const std::size_t blockHeight = detail::BlockCount(height);
    const std::size_t blockBytes = BlockBytes(kind);
    detail::Rgba colors[16];
    std::uint8_t alpha[16];
    std::uint8_t red[16];
    std::uint8_t green[16];

    for (std::size_t by = 0; by < blockHeight; ++by)
    {
        for (std::size_t bx = 0; bx < blockWidth; ++bx)
        {
            const std::size_t blockOffset = (by * blockWidth + bx) * blockBytes;
            const std::uint8_t* const block = source + blockOffset;
            switch (kind)
            {
            case Kind::bc1:
                detail::DecodeColorBlock(block, colors, true);
                break;
            case Kind::bc2:
                detail::DecodeColorBlock(block + 8, colors, false);
                for (std::size_t index = 0; index < 16; ++index)
                {
                    const std::uint8_t nibble = static_cast<std::uint8_t>(
                        (block[index / 2] >> (4U * static_cast<unsigned>(index & 1U))) & 15U);
                    colors[index].a = static_cast<std::uint8_t>(nibble * 17U);
                }
                break;
            case Kind::bc3:
                detail::DecodeColorBlock(block + 8, colors, false);
                detail::DecodeAlphaBlock(block, alpha);
                for (std::size_t index = 0; index < 16; ++index)
                    colors[index].a = alpha[index];
                break;
            case Kind::bc4:
                detail::DecodeAlphaBlock(block, red);
                for (std::size_t index = 0; index < 16; ++index)
                    colors[index] = { red[index], red[index], red[index], 255 };
                break;
            case Kind::bc5:
                detail::DecodeAlphaBlock(block, red);
                detail::DecodeAlphaBlock(block + 8, green);
                for (std::size_t index = 0; index < 16; ++index)
                    colors[index] = { red[index], green[index], 0, 255 };
                break;
            case Kind::none:
                return false;
            }

            for (std::size_t py = 0; py < 4; ++py)
            {
                const std::size_t y = by * 4 + py;
                if (y >= height)
                    break;
                for (std::size_t px = 0; px < 4; ++px)
                {
                    const std::size_t x = bx * 4 + px;
                    if (x >= width)
                        break;
                    detail::StorePixel(destination, width, x, y, colors[py * 4 + px]);
                }
            }
        }
    }
    return true;
}
} // namespace xray::render::ios_bc
