#include "src/xrEngine/ios/ios_texture_memory.h"

#include <cstdlib>
#include <iostream>

namespace
{
void require(const bool condition, const char* message)
{
    if (!condition)
    {
        std::cerr << "FAIL: " << message << '\n';
        std::exit(1);
    }
}
} // namespace

int main()
{
    using ios_texture_memory::Rgba8MipChainBytes;
    require(Rgba8MipChainBytes(1, 1, 1, 1) == 4, "one RGBA8 texel must use four bytes");
    require(Rgba8MipChainBytes(4, 4, 1, 3) == 84, "2D mip chain accounting must include every level");
    require(Rgba8MipChainBytes(4, 4, 1, 3, 6) == 504, "cube accounting must include six faces");
    require(Rgba8MipChainBytes(4, 4, 4, 3) == 292, "3D mip chain must halve depth as well");
    require(Rgba8MipChainBytes(1024, 1024, 1, 11) == 5592404,
        "1024 RGBA8 full mip chain must report the decoded allocation");

    std::cout << "iOS texture memory policy: PASS\n";
}
