#include "src/Layers/xrRenderGL/ios_bc_gli_format.h"

#include <array>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <vector>

namespace
{
using Byte = std::uint8_t;
using xray::render::ios_bc::BlockBytes;
using xray::render::ios_bc::DecodeLevel;
using xray::render::ios_bc::DecodedLevelBytes;
using xray::render::ios_bc::EncodedLevelBytes;
using xray::render::ios_bc::FormatInfo;
using xray::render::ios_bc::FromGliFormat;
using xray::render::ios_bc::Kind;
using xray::render::ios_bc::Layout;
using xray::render::ios_bc::Storage;
using xray::render::ios_bc::SwizzlePolicy;
using xray::render::ios_bc::Transfer;

[[noreturn]] void Fail(const char* const message)
{
    std::cerr << "FAIL: " << message << '\n';
    std::exit(1);
}

void Require(const bool condition, const char* const message)
{
    if (!condition)
        Fail(message);
}

void SetLe16(std::array<Byte, 8>& bytes, const std::size_t offset, const std::uint16_t value)
{
    bytes[offset] = static_cast<Byte>(value & 255U);
    bytes[offset + 1] = static_cast<Byte>(value >> 8U);
}

void SetLe32(std::array<Byte, 8>& bytes, const std::size_t offset, const std::uint32_t value)
{
    for (unsigned index = 0; index < 4; ++index)
        bytes[offset + index] = static_cast<Byte>(value >> (8U * index));
}

void SetAlphaSelectors(std::array<Byte, 8>& bytes, const std::uint64_t selectors)
{
    for (unsigned index = 0; index < 6; ++index)
        bytes[index + 2] = static_cast<Byte>(selectors >> (8U * index));
}

std::array<Byte, 8> ColorBlock(const std::uint16_t color0, const std::uint16_t color1,
    const std::uint32_t selectors)
{
    std::array<Byte, 8> block{};
    SetLe16(block, 0, color0);
    SetLe16(block, 2, color1);
    SetLe32(block, 4, selectors);
    return block;
}

std::array<Byte, 8> AlphaBlock(const Byte alpha0, const Byte alpha1, const std::uint64_t selectors)
{
    std::array<Byte, 8> block{};
    block[0] = alpha0;
    block[1] = alpha1;
    SetAlphaSelectors(block, selectors);
    return block;
}

void CopyBlock(std::array<Byte, 16>& destination, const std::size_t offset,
    const std::array<Byte, 8>& source)
{
    for (std::size_t index = 0; index < source.size(); ++index)
        destination[offset + index] = source[index];
}

void RequirePixel(const std::vector<Byte>& pixels, const std::size_t width,
    const std::size_t x, const std::size_t y, const Byte red, const Byte green,
    const Byte blue, const Byte alpha, const char* const message)
{
    const std::size_t offset = (y * width + x) * 4;
    Require(pixels[offset] == red && pixels[offset + 1] == green
            && pixels[offset + 2] == blue && pixels[offset + 3] == alpha,
        message);
}

std::vector<Byte> DecodeBlock(const Kind kind, const std::vector<Byte>& source)
{
    std::vector<Byte> output(DecodedLevelBytes(4, 4));
    Require(DecodeLevel(kind, source.data(), source.size(), 4, 4, output.data(), output.size()),
        "synthetic block must decode");
    return output;
}

void TestBc1FourColor()
{
    const std::array<Byte, 8> block = ColorBlock(0xf800U, 0x001fU, 0xe4U);
    const std::vector<Byte> source(block.begin(), block.end());
    const std::vector<Byte> output = DecodeBlock(Kind::bc1, source);
    RequirePixel(output, 4, 0, 0, 255, 0, 0, 255, "BC1 selector zero must select color0");
    RequirePixel(output, 4, 1, 0, 0, 0, 255, 255, "BC1 selector one must select color1");
    RequirePixel(output, 4, 2, 0, 170, 0, 85, 255, "BC1 selector two must interpolate 2/3");
    RequirePixel(output, 4, 3, 0, 85, 0, 170, 255, "BC1 selector three must interpolate 1/3");
}

void TestBc1ThreeColorAlpha()
{
    const std::array<Byte, 8> block = ColorBlock(0x0000U, 0xffffU, 0xe4U);
    const std::vector<Byte> source(block.begin(), block.end());
    const std::vector<Byte> output = DecodeBlock(Kind::bc1, source);
    RequirePixel(output, 4, 0, 0, 0, 0, 0, 255, "BC1 three-color selector zero must select black");
    RequirePixel(output, 4, 1, 0, 255, 255, 255, 255, "BC1 three-color selector one must select white");
    RequirePixel(output, 4, 2, 0, 127, 127, 127, 255, "BC1 three-color selector two must average");
    RequirePixel(output, 4, 3, 0, 0, 0, 0, 0, "BC1 three-color selector three must be transparent");
}

void TestBc2Alpha()
{
    std::array<Byte, 16> block{};
    block[0] = 0xf0U;
    CopyBlock(block, 8, ColorBlock(0xf800U, 0x001fU, 0x04U));
    const std::vector<Byte> source(block.begin(), block.end());
    const std::vector<Byte> output = DecodeBlock(Kind::bc2, source);
    RequirePixel(output, 4, 0, 0, 255, 0, 0, 0, "BC2 low alpha nibble must expand to zero");
    RequirePixel(output, 4, 1, 0, 0, 0, 255, 255, "BC2 high alpha nibble must expand to 255");
}

void TestBc3BothAlphaBranches()
{
    std::array<Byte, 16> highBranch{};
    CopyBlock(highBranch, 0, AlphaBlock(200, 100, 0x0688U));
    CopyBlock(highBranch, 8, ColorBlock(0xf800U, 0x001fU, 0));
    const std::vector<Byte> highOutput = DecodeBlock(
        Kind::bc3, std::vector<Byte>(highBranch.begin(), highBranch.end()));
    RequirePixel(highOutput, 4, 0, 0, 255, 0, 0, 200, "BC3 seven-alpha branch selector zero must select alpha0");
    RequirePixel(highOutput, 4, 1, 0, 255, 0, 0, 100, "BC3 seven-alpha branch selector one must select alpha1");
    RequirePixel(highOutput, 4, 2, 0, 255, 0, 0, 185, "BC3 seven-alpha branch must interpolate");

    std::array<Byte, 16> lowBranch{};
    CopyBlock(lowBranch, 0, AlphaBlock(10, 20, 62U));
    CopyBlock(lowBranch, 8, ColorBlock(0xf800U, 0x001fU, 0));
    const std::vector<Byte> lowOutput = DecodeBlock(
        Kind::bc3, std::vector<Byte>(lowBranch.begin(), lowBranch.end()));
    RequirePixel(lowOutput, 4, 0, 0, 255, 0, 0, 0, "BC3 six-alpha branch selector six must select zero");
    RequirePixel(lowOutput, 4, 1, 0, 255, 0, 0, 255, "BC3 six-alpha branch selector seven must select 255");
}

void TestBc4AndBc5Layouts()
{
    const std::array<Byte, 8> red = AlphaBlock(10, 20, 0x0f88U);
    const std::vector<Byte> bc4Output = DecodeBlock(Kind::bc4, std::vector<Byte>(red.begin(), red.end()));
    RequirePixel(bc4Output, 4, 0, 0, 10, 10, 10, 255, "BC4 must decode as RRR1");
    RequirePixel(bc4Output, 4, 1, 0, 20, 20, 20, 255, "BC4 must retain alpha1 in RRR1");
    RequirePixel(bc4Output, 4, 2, 0, 0, 0, 0, 255, "BC4 selector six must decode as zero");
    RequirePixel(bc4Output, 4, 3, 0, 255, 255, 255, 255, "BC4 selector seven must decode as 255");

    std::array<Byte, 16> bc5{};
    CopyBlock(bc5, 0, red);
    CopyBlock(bc5, 8, AlphaBlock(30, 40, 0x0f88U));
    const std::vector<Byte> bc5Output = DecodeBlock(Kind::bc5, std::vector<Byte>(bc5.begin(), bc5.end()));
    RequirePixel(bc5Output, 4, 0, 0, 10, 30, 0, 255, "BC5 must decode as RG01");
    RequirePixel(bc5Output, 4, 1, 0, 20, 40, 0, 255, "BC5 must retain second channel in RG01");
    RequirePixel(bc5Output, 4, 2, 0, 0, 0, 0, 255, "BC5 zero selectors must preserve RG01 constants");
    RequirePixel(bc5Output, 4, 3, 0, 255, 255, 0, 255, "BC5 255 selectors must preserve RG01 constants");
}

void TestEdgeExtentsAndBounds()
{
    struct Extent
    {
        std::size_t width;
        std::size_t height;
    };
    constexpr std::array<Extent, 4> extents = { Extent{ 1, 1 }, Extent{ 3, 5 }, Extent{ 5, 3 }, Extent{ 9, 7 } };
    constexpr std::array<Kind, 5> kinds = { Kind::bc1, Kind::bc2, Kind::bc3, Kind::bc4, Kind::bc5 };

    for (const Kind kind : kinds)
    {
        for (const Extent extent : extents)
        {
            const std::size_t encodedBytes = EncodedLevelBytes(kind, extent.width, extent.height);
            const std::size_t decodedBytes = DecodedLevelBytes(extent.width, extent.height);
            Require(encodedBytes != 0 && decodedBytes != 0, "valid BC extent must have exact byte sizes");
            std::vector<Byte> source(encodedBytes, 0);
            std::vector<Byte> destination(decodedBytes + 2, 0xa5U);
            Require(DecodeLevel(kind, source.data(), source.size(), extent.width, extent.height,
                        destination.data() + 1, decodedBytes),
                "edge extent must decode safely");
            Require(destination.front() == 0xa5U && destination.back() == 0xa5U,
                "edge decode must not overwrite destination guards");
        }
    }

    std::array<Byte, 16> source{};
    std::array<Byte, 64> destination{};
    Require(!DecodeLevel(Kind::bc1, source.data(), 7, 4, 4, destination.data(), destination.size()),
        "truncated BC source must be rejected");
    Require(!DecodeLevel(Kind::bc1, source.data(), 8, 4, 4, destination.data(), 63),
        "undersized RGBA destination must be rejected");
    Require(!DecodeLevel(Kind::bc1, nullptr, 8, 4, 4, destination.data(), destination.size()),
        "null source must be rejected");
    Require(!DecodeLevel(Kind::bc1, source.data(), 8, 4, 4, nullptr, 64),
        "null destination must be rejected");
    Require(!DecodeLevel(Kind::none, source.data(), source.size(), 4, 4, destination.data(), destination.size()),
        "unsupported BC kind must be rejected");
    Require(!DecodeLevel(Kind::bc1, source.data(), source.size(), 0, 4, destination.data(), destination.size()),
        "zero extent must be rejected");
    Require(EncodedLevelBytes(Kind::bc1, std::numeric_limits<std::size_t>::max(), 4) == 0,
        "encoded byte calculation must reject overflow");
    Require(DecodedLevelBytes(std::numeric_limits<std::size_t>::max(), 2) == 0,
        "decoded byte calculation must reject overflow");
    Require(BlockBytes(Kind::none) == 0, "unsupported BC kind must have no block size");
}

void RequireFormatInfo(const gli::format format, const Kind kind, const Transfer transfer,
    const Layout layout, const char* const message)
{
    const FormatInfo info = FromGliFormat(format);
    Require(info.kind == kind && info.sourceTransfer == transfer && info.decodedLayout == layout,
        message);
    Require(BlockBytes(info.kind) == gli::block_size(format),
        "codec block size must agree with the mapped GLI format");
    const auto plan = xray::render::ios_bc::CurrentUploadPlan(info);
    Require(plan.storage == Storage::rgba8Unorm, "current iOS BC fallback must remain linear RGBA8");
    Require(plan.swizzle == SwizzlePolicy::ignoreGliSource, "current iOS BC fallback must ignore GLI swizzles");
}

void TestGliMappingsAndFixtures()
{
    RequireFormatInfo(gli::FORMAT_RGB_DXT1_UNORM_BLOCK8, Kind::bc1, Transfer::linear, Layout::rgba,
        "RGB DXT1 UNORM mapping changed");
    RequireFormatInfo(gli::FORMAT_RGB_DXT1_SRGB_BLOCK8, Kind::bc1, Transfer::srgb, Layout::rgba,
        "RGB DXT1 sRGB mapping changed");
    RequireFormatInfo(gli::FORMAT_RGBA_DXT1_UNORM_BLOCK8, Kind::bc1, Transfer::linear, Layout::rgba,
        "RGBA DXT1 UNORM mapping changed");
    RequireFormatInfo(gli::FORMAT_RGBA_DXT1_SRGB_BLOCK8, Kind::bc1, Transfer::srgb, Layout::rgba,
        "RGBA DXT1 sRGB mapping changed");
    RequireFormatInfo(gli::FORMAT_RGBA_DXT3_UNORM_BLOCK16, Kind::bc2, Transfer::linear, Layout::rgba,
        "DXT3 UNORM mapping changed");
    RequireFormatInfo(gli::FORMAT_RGBA_DXT3_SRGB_BLOCK16, Kind::bc2, Transfer::srgb, Layout::rgba,
        "DXT3 sRGB mapping changed");
    RequireFormatInfo(gli::FORMAT_RGBA_DXT5_UNORM_BLOCK16, Kind::bc3, Transfer::linear, Layout::rgba,
        "DXT5 UNORM mapping changed");
    RequireFormatInfo(gli::FORMAT_RGBA_DXT5_SRGB_BLOCK16, Kind::bc3, Transfer::srgb, Layout::rgba,
        "DXT5 sRGB mapping changed");
    RequireFormatInfo(gli::FORMAT_R_ATI1N_UNORM_BLOCK8, Kind::bc4, Transfer::linear, Layout::rrr1,
        "BC4 mapping changed");
    RequireFormatInfo(gli::FORMAT_RG_ATI2N_UNORM_BLOCK16, Kind::bc5, Transfer::linear, Layout::rg01,
        "BC5 mapping changed");
    Require(FromGliFormat(gli::FORMAT_R_ATI1N_SNORM_BLOCK8).kind == Kind::none,
        "unsupported signed BC4 must stay on the existing non-BC path");

    struct Fixture
    {
        const char* path;
        gli::format format;
    };
    constexpr std::array<Fixture, 5> fixtures = {
        Fixture{ "Externals/gli/data/kueken7_rgb_dxt1_unorm.ktx", gli::FORMAT_RGB_DXT1_UNORM_BLOCK8 },
        Fixture{ "Externals/gli/data/kueken7_rgba_dxt1_srgb.dds", gli::FORMAT_RGBA_DXT1_SRGB_BLOCK8 },
        Fixture{ "Externals/gli/data/kueken7_rgba_dxt5_srgb.dds", gli::FORMAT_RGBA_DXT5_SRGB_BLOCK16 },
        Fixture{ "Externals/gli/data/kueken7_r_ati1n_unorm.dds", gli::FORMAT_R_ATI1N_UNORM_BLOCK8 },
        Fixture{ "Externals/gli/data/kueken7_rg_ati2n_unorm.dds", gli::FORMAT_RG_ATI2N_UNORM_BLOCK16 },
    };
    for (const Fixture fixtureSpec : fixtures)
    {
        const gli::texture fixture = gli::load(fixtureSpec.path);
        Require(!fixture.empty(), "real GLI compressed texture fixture must load");
        if (fixture.format() != fixtureSpec.format)
        {
            std::cerr << "fixture format mismatch: " << fixtureSpec.path
                      << " parsed=" << static_cast<int>(fixture.format())
                      << " expected=" << static_cast<int>(fixtureSpec.format) << '\n';
            Fail("real GLI fixture must parse to its exact format");
        }
        const FormatInfo info = FromGliFormat(fixture.format());
        Require(info.kind != Kind::none, "real GLI fixture must use the sole iOS BC mapping");
        Require(fixture.layers() != 0 && fixture.faces() != 0 && fixture.levels() != 0,
            "real GLI fixture must contain a base subresource");

        const gli::texture::extent_type extent = fixture.extent(0);
        const std::size_t width = static_cast<std::size_t>(extent.x);
        const std::size_t height = static_cast<std::size_t>(extent.y);
        const std::size_t expectedLevelBytes = EncodedLevelBytes(info.kind, width, height);
        const std::size_t actualLevelBytes = fixture.size(0);
        const auto* const source = static_cast<const Byte*>(fixture.data(0, 0, 0));
        Require(source != nullptr && actualLevelBytes != 0, "real GLI fixture must expose base-level data");
        Require(actualLevelBytes >= expectedLevelBytes,
            "real GLI fixture base level must contain the complete encoded image");

        const std::size_t decodedBytes = DecodedLevelBytes(width, height);
        std::vector<Byte> decoded(decodedBytes);
        Require(DecodeLevel(info.kind, source, actualLevelBytes, width, height,
                    decoded.data(), decoded.size()),
            "real GLI fixture base level must decode using its actual byte size");
    }

    const Storage representableSrgbStorage = Storage::srgb8Alpha8;
    Require(representableSrgbStorage == Storage::srgb8Alpha8,
        "sRGB storage must remain representable for the later visual-validation slice");
}
} // namespace

int main()
{
    TestBc1FourColor();
    TestBc1ThreeColorAlpha();
    TestBc2Alpha();
    TestBc3BothAlphaBranches();
    TestBc4AndBc5Layouts();
    TestEdgeExtentsAndBounds();
    TestGliMappingsAndFixtures();
    std::cout << "iOS BC fallback contract: PASS\n";
}
