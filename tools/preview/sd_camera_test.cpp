#include "sd_camera.h"
#include <cassert>
#include <iostream>
#include <array>

int main()
{
    uint32_t seed = 12345;
    for (unsigned weight = 0; weight <= 32; ++weight) {
        for (unsigned i = 0; i < 65536; ++i) {
            seed = seed * 1664525 + 1013904223;
            uint16_t a = i, b = seed >> 16, out = 0;
            SdCamera::blend565(&a, &b, &out, 1, weight);
            const unsigned red = (((a >> 11) * (32-weight) + (b >> 11) * weight) >> 5) << 11;
            const unsigned green = ((((a >> 5) & 63) * (32-weight) + ((b >> 5) & 63) * weight) >> 5) << 5;
            const unsigned blue = ((a & 31) * (32-weight) + (b & 31) * weight) >> 5;
            assert(out == (red | green | blue));
            SdCamera::blend565(&a, &b, &b, 1, weight);
            assert(b == out);
        }
    }
    assert(SdCamera::weight(0, 4) == 0);
    assert(SdCamera::weight(4, 4) == 32);
    assert(SdCamera::weight(1, 4) < SdCamera::weight(2, 4));
    assert(SdCamera::weight(2, 4) < SdCamera::weight(3, 4));
    for (unsigned alpha = 0; alpha <= 256; ++alpha) {
        for (unsigned i = 0; i < 65536; ++i) {
            seed = seed * 1664525 + 1013904223;
            uint16_t a = i, b = seed >> 16, out = 0;
            SdCamera::mix565(&a, &b, &out, 1, alpha);
            const unsigned r = (((a >> 11)*(256-alpha)+(b >> 11)*alpha) >> 8) << 11;
            const unsigned g = ((((a >> 5)&63)*(256-alpha)+((b >> 5)&63)*alpha) >> 8) << 5;
            const unsigned blue = ((a&31)*(256-alpha)+(b&31)*alpha) >> 8;
            assert(out == (r|g|blue));
            SdCamera::mix565(&a, &b, &b, 1, alpha);
            assert(b == out);
        }
    }
    const auto duration = SdCamera::duration_us(84, 30);
    assert(duration == 1866666);
    assert(SdCamera::sample(0, 84, 30) == 0);
    assert(SdCamera::sample(duration/2, 84, 30) == 41);
    assert(SdCamera::sample(duration, 84, 30) == 83);
    assert(SdCamera::sample(duration*2, 84, 30) == 83);
    assert(SdCamera::duration_us(56, 30, 1, 1) == duration);
    assert(SdCamera::sample(duration/2, 56, 30, 1, 1) == 27);
    assert(SdCamera::sample(duration, 56, 30, 1, 1) == 55);
    unsigned previous = 0, full_tail = 0;
    for (unsigned frame = 0; frame < 84; ++frame) {
        const unsigned alpha = SdCamera::landing_weight(frame, 84);
        assert(alpha >= previous && alpha <= 256);
        previous = alpha;
        if (alpha == 256) {
            ++full_tail;
            const uint16_t target = uint16_t(frame*643), camera = 0xa5a5;
            uint16_t out = 0;
            SdCamera::mix565(&camera, &target, &out, 1, alpha);
            assert(out == target);
        }
    }
    assert(full_tail >= 6);
    std::array<uint16_t, 15> flat{}, scratch{};
    flat.fill(0xad55);
    SdCamera::soften565(flat.data(), scratch.data(), 5, 3);
    for (auto pixel : flat) assert(pixel == 0xad55);
    uint16_t one = 0x5abe, one_scratch = 0;
    SdCamera::soften565(&one, &one_scratch, 1, 1);
    assert(one == 0x5abe);
    std::cout << "SD camera endpoint blending: 2162688 cases passed\n";
    std::cout << "1.5x timeline, exact landing tail, 16842752 alpha cases passed\n";
}
