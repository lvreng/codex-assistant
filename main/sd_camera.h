#pragma once
#include <cstddef>
#include <cstdint>
#include <algorithm>
#include <cstring>

namespace SdCamera {
constexpr size_t MaxFrameBytes = 160 * 1024;
constexpr uint32_t JoinFrames = 4;
constexpr uint32_t StateJoinFrames = 12;
constexpr uint32_t SpeedNumerator = 3, SpeedDenominator = 2;

inline int64_t duration_us(uint32_t frames, uint16_t fps,
                           unsigned numerator = SpeedNumerator, unsigned denominator = SpeedDenominator)
{
    return int64_t(frames) * 1000000 * denominator /
           (std::max<uint16_t>(1, fps) * std::max(1U, numerator));
}

inline uint32_t sample(int64_t elapsed, uint32_t frames, uint16_t fps,
                       unsigned numerator = SpeedNumerator, unsigned denominator = SpeedDenominator)
{
    if (frames < 2 || elapsed <= 0) return 0;
    const int64_t duration = duration_us(frames, fps, numerator, denominator);
    return elapsed >= duration ? frames - 1 : uint32_t(elapsed * (frames - 1) / duration);
}

inline unsigned smooth_weight(float t)
{
    t = std::clamp(t, 0.0f, 1.0f);
    return unsigned(t*t*t*(t*(t*6-15)+10)*256 + .5f);
}

inline unsigned landing_weight(uint32_t frame, uint32_t frames)
{
    if (frames < 2) return 256;
    const float phase = float(frame) / (frames-1);
    // Finish the join before the camera ends: the tail is actual loop frames.
    return smooth_weight((phase - .64f) / .28f);
}

inline void mix565(const uint16_t *from, const uint16_t *to,
                   uint16_t *out, size_t pixels, unsigned alpha)
{
    if (!alpha) {
        if (out != from) std::memcpy(out, from, pixels * sizeof(uint16_t));
        return;
    }
    if (alpha >= 256) {
        if (out != to) std::memcpy(out, to, pixels * sizeof(uint16_t));
        return;
    }
    for (size_t i = 0; i < pixels; ++i) {
        const uint32_t a = from[i], b = to[i];
        // Expand the red/blue gap before using 8-bit weights; otherwise red's
        // fractional bits leak into blue (the older 5-bit blend had enough gap).
        const uint32_t ar = ((a & 0xf800) << 2) | (a & 31);
        const uint32_t br = ((b & 0xf800) << 2) | (b & 31);
        const uint32_t rb = (ar*(256-alpha) + br*alpha) >> 8;
        const uint32_t g = (((a & 0x07e0)*(256-alpha) + (b & 0x07e0)*alpha) >> 8) & 0x07e0;
        out[i] = uint16_t(((rb >> 2) & 0xf800) | (rb & 31) | g);
    }
}

inline void soften565(uint16_t *image, uint16_t *scratch, int width, int height)
{
    // Only the frozen departure frame is softened; no steady-state quality loss.
    for (int pass = 0; pass < 2; ++pass) {
        const uint16_t *src = pass ? scratch : image;
        uint16_t *dst = pass ? image : scratch;
        for (int y = 0; y < height; ++y) for (int x = 0; x < width; ++x) {
            unsigned red = 0, green = 0, blue = 0;
            for (int k = -2; k <= 2; ++k) {
                const int sx = pass ? x : std::clamp(x+k, 0, width-1);
                const int sy = pass ? std::clamp(y+k, 0, height-1) : y;
                const uint16_t p = src[sy*width+sx];
                red += p >> 11;
                green += (p >> 5) & 63;
                blue += p & 31;
            }
            dst[y*width+x] = uint16_t((red/5 << 11) | (green/5 << 5) | (blue/5));
        }
    }
}
inline unsigned weight(unsigned step, unsigned count)
{
    if (!count || step >= count) return 32;
    const float t = float(step) / count;
    return unsigned(t * t * (3.0f - 2.0f * t) * 32.0f + .5f);
}
inline void blend565(const uint16_t *from, const uint16_t *to,
                     uint16_t *out, size_t pixels, unsigned weight)
{
    if (weight > 32) weight = 32;
    for (size_t i = 0; i < pixels; ++i) {
        const uint32_t a = (uint32_t(from[i]) | (uint32_t(from[i]) << 16)) & 0x07e0f81f;
        const uint32_t b = (uint32_t(to[i]) | (uint32_t(to[i]) << 16)) & 0x07e0f81f;
        const uint32_t mixed = ((a * (32-weight) + b * weight) >> 5) & 0x07e0f81f;
        out[i] = uint16_t(mixed | (mixed >> 16));
    }
}
}
