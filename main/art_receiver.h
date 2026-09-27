#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>

namespace Artwork {
constexpr size_t Capacity = 96 * 1024;
constexpr unsigned Width = 560;
constexpr unsigned Height = 416;

inline uint16_t u16(const uint8_t *p) { return p[0] | (uint16_t(p[1]) << 8); }
inline uint32_t u32(const uint8_t *p)
{
    return u16(p) | (uint32_t(u16(p + 2)) << 16);
}

class Assembly {
public:
    enum Result { Invalid, Partial, Complete, Clear, Stale };
    uint16_t frame = 0;
    uint8_t key = 0;
    uint32_t size = 0;

    Result feed(const uint8_t *p, size_t length, uint16_t wanted, uint8_t *buffer)
    {
        if (length < 14 || length > 2062 || p[0] != 3 || p[1] > 4) return Invalid;
        const uint16_t next_frame = u16(p + 2), generation = u16(p + 4);
        if (generation != wanted) return Stale;
        const uint32_t offset = u32(p + 6), total = u32(p + 10);
        const size_t bytes = length - 14;
        if (p[1] == 0) {
            if (offset || total || bytes) return Invalid;
            received_ = size = 0;
            frame = next_frame;
            key = 0;
            return Clear;
        }
        if (!buffer || !total || total > Capacity || !bytes ||
            offset > total || bytes > total - offset) return Invalid;
        if (offset == 0) {
            frame = next_frame;
            generation_ = generation;
            key = p[1];
            size = total;
            received_ = 0;
        }
        if (frame != next_frame || generation_ != generation || key != p[1] ||
            size != total || offset != received_) return Invalid;
        std::memcpy(buffer + offset, p + 14, bytes);
        received_ += bytes;
        return received_ == size ? Complete : Partial;
    }

private:
    uint32_t received_ = 0;
    uint16_t generation_ = 0;
};
} // namespace Artwork
