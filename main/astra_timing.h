#pragma once

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>

namespace AstraTiming {

// SD clips contain a fixed 1:1.5 period distribution, expressed in deciseconds.
struct Periods {
    uint16_t minimum;
    uint16_t maximum;
};

inline Periods from_minimum(int value)
{
    const auto low = static_cast<uint16_t>(std::clamp(value, 10, 800));
    return {low, static_cast<uint16_t>((low * 3 + 1) / 2)};
}

inline Periods from_maximum(int value)
{
    return from_minimum((std::clamp(value, 15, 1200) * 2 + 1) / 3);
}

inline double speed(uint8_t style, uint16_t minimum)
{
    return (style == 0 ? 50.0 : 100.0) / from_minimum(minimum).minimum;
}

struct Sample {
    uint32_t first;
    uint32_t second;
    uint8_t alpha;
};

inline uint16_t add_saturated(uint16_t background, uint16_t light)
{
    const unsigned red = std::min(31U, unsigned(background >> 11) + unsigned(light >> 11));
    const unsigned green = std::min(63U, unsigned((background >> 5) & 63) + unsigned((light >> 5) & 63));
    const unsigned blue = std::min(31U, unsigned(background & 31) + unsigned(light & 31));
    return uint16_t((red << 11) | (green << 5) | blue);
}

inline uint16_t add_light(uint16_t background, uint16_t a, uint16_t b, unsigned alpha)
{
    // RGB565 lanes can be interpolated together with five fractional bits.
    const uint32_t left = (uint32_t(a) | (uint32_t(a) << 16)) & 0x07E0F81F;
    const uint32_t right = (uint32_t(b) | (uint32_t(b) << 16)) & 0x07E0F81F;
    const uint32_t mix = ((left * (32-alpha) + right * alpha) >> 5) & 0x07E0F81F;
    const uint16_t light = uint16_t(mix | (mix >> 16));
    return add_saturated(background, light);
}

inline void compose(uint16_t *background, const uint16_t *a, const uint16_t *b,
                    size_t pixels, uint8_t alpha)
{
    const unsigned weight = (unsigned(alpha)*32 + 127)/255;
    if (!weight || a == b || weight == 32) {
        const auto *light = weight == 32 ? b : a;
        for (size_t i = 0; i < pixels; ++i) {
            if (light[i]) background[i] = add_saturated(background[i], light[i]);
        }
        return;
    }
    for (size_t i = 0; i < pixels; ++i) {
        if (a[i] || b[i]) background[i] = add_light(background[i], a[i], b[i], weight);
    }
}

class Clock {
public:
    bool valid = false;
    double position = 0;

    void reset(int64_t now, uint32_t frame, double speed)
    {
        valid = true;
        position = frame;
        last_ = now;
        speed_ = speed;
    }

    Sample sample(int64_t now, uint16_t fps, double speed, uint32_t count)
    {
        // Finish the elapsed interval at the previous speed before accepting a change.
        position += std::max<int64_t>(0, now - last_) * fps * speed_ / 1000000.0;
        last_ = now;
        speed_ = speed;
        const double phase = std::fmod(position, count);
        const auto first = static_cast<uint32_t>(phase);
        return {first, (first + 1) % count,
                static_cast<uint8_t>(std::lround((phase - first) * 255))};
    }

private:
    int64_t last_ = 0;
    double speed_ = 1;
};
} // namespace AstraTiming
