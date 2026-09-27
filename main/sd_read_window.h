#pragma once

#include <cstdint>

namespace SdReadWindow {
constexpr uint32_t SectorBytes = 512;
constexpr uint32_t ExtraBytes = SectorBytes * 2;

struct Window {
    uint32_t offset;
    uint32_t bytes;
    uint32_t prefix;
};

inline Window aligned(uint32_t offset, uint32_t bytes)
{
    const uint32_t prefix = offset % SectorBytes;
    return {offset-prefix, ((prefix+bytes+SectorBytes-1)/SectorBytes)*SectorBytes, prefix};
}
} // namespace SdReadWindow
