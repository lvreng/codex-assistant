#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>

#ifdef ESP_PLATFORM
#include "esp_attr.h"
#endif

namespace MediaCrc {
constexpr auto tables()
{
    std::array<std::array<uint32_t, 256>, 4> result{};
    for (unsigned i = 0; i < 256; ++i) {
        uint32_t value = i;
        for (unsigned bit = 0; bit < 8; ++bit)
            value = (value >> 1) ^ ((value & 1) ? 0xEDB88320U : 0);
        result[0][i] = value;
    }
    for (unsigned row = 1; row < 4; ++row)
        for (unsigned i = 0; i < 256; ++i) {
            const uint32_t value = result[row-1][i];
            result[row][i] = (value >> 8) ^ result[0][value & 255];
        }
    return result;
}

#ifdef ESP_PLATFORM
DRAM_ATTR
#endif
inline const auto Table = tables();

inline uint32_t update(uint32_t seed, const uint8_t *data, size_t size)
{
    uint32_t crc = ~seed;
    while (size >= 4) {
        uint32_t word;
        std::memcpy(&word, data, sizeof(word));
#if __BYTE_ORDER__ == __ORDER_BIG_ENDIAN__
        word = __builtin_bswap32(word);
#endif
        crc ^= word;
        crc = Table[3][crc & 255] ^ Table[2][(crc >> 8) & 255] ^
              Table[1][(crc >> 16) & 255] ^ Table[0][crc >> 24];
        data += 4;
        size -= 4;
    }
    while (size--) crc = Table[0][(crc ^ *data++) & 255] ^ (crc >> 8);
    return ~crc;
}
} // namespace MediaCrc
