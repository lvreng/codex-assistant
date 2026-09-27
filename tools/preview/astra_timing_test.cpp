#include "astra_timing.h"
#include "sd_read_window.h"
#include "media_crc.h"
#include <cassert>

int main()
{
    const uint8_t check[] = "123456789";
    assert(MediaCrc::update(0, check, 9) == 0xCBF43926);
    std::array<uint8_t, 2053> input;
    uint32_t rng = 73;
    for (auto &byte : input) {
        rng = rng*1664525U+1013904223U;
        byte = rng >> 24;
    }
    for (size_t offset = 0; offset < 4; ++offset)
        for (size_t length = 0; length <= 2048; length += 7) {
            uint32_t expected = ~uint32_t(length);
            for (size_t i = 0; i < length; ++i) {
                expected ^= input[offset+i];
                for (unsigned bit = 0; bit < 8; ++bit)
                    expected = (expected >> 1) ^ ((expected & 1) ? 0xEDB88320U : 0);
            }
            assert(MediaCrc::update(length, input.data()+offset, length) == ~expected);
            const size_t part = length/2;
            const auto prefix = MediaCrc::update(length, input.data()+offset, part);
            assert(MediaCrc::update(prefix, input.data()+offset+part, length-part) == ~expected);
        }
    for (uint32_t offset = 100000; offset < 101024; ++offset) {
        for (uint32_t bytes : {1U, 511U, 512U, 513U, 163840U}) {
            const auto window = SdReadWindow::aligned(offset, bytes);
            assert(window.offset%512 == 0 && window.bytes%512 == 0);
            assert(window.offset+window.prefix == offset);
            assert(window.bytes >= window.prefix+bytes);
            assert(window.bytes < bytes+SdReadWindow::ExtraBytes);
        }
    }
    using namespace AstraTiming;
    assert(from_minimum(50).maximum == 75);
    assert(from_minimum(100).maximum == 150);
    assert(from_minimum(-5).minimum == 10);
    assert(from_minimum(1200).minimum == 800);
    assert(from_maximum(1200).maximum == 1200);
    assert(from_maximum(0).minimum == 10);
    assert(from_maximum(155).minimum > 100);
    assert(from_maximum(145).minimum < 100);
    assert(speed(0, 50) == 1 && speed(1, 100) == 1);
    assert(speed(1, 200) == .5 && speed(1, 50) == 2);

    assert(add_light(0xFFFF, 0xFFFF, 0xFFFF, 16) == 0xFFFF);
    assert(add_light(0x1234, 0, 0, 0) == 0x1234);
    uint32_t random = 1;
    for (unsigned test = 0; test < 100000; ++test) {
        auto next = [&]() { random = random * 1664525U + 1013904223U; return uint16_t(random >> 16); };
        const auto bg = next(), a = next(), b = next();
        const unsigned alpha = test % 33;
        unsigned expected = 0;
        for (unsigned shift : {0U, 5U, 11U}) {
            const unsigned mask = shift == 5 ? 63 : 31;
            const unsigned light = (((a >> shift) & mask) * (32-alpha) + ((b >> shift) & mask) * alpha) >> 5;
            expected |= std::min(mask, ((bg >> shift) & mask) + light) << shift;
        }
        assert(add_light(bg, a, b, alpha) == expected);
        uint16_t composed = bg;
        const auto fraction = uint8_t((alpha*255+16)/32);
        compose(&composed, &a, &b, 1, fraction);
        assert(composed == expected);
        composed = bg;
        compose(&composed, &a, &a, 1, fraction);
        assert(composed == add_light(bg, a, a, 0));
    }

    Clock clock;
    Clock background;
    background.reset(1000000, 0, 1);
    clock.reset(1000000, 0, 1);
    assert(clock.sample(2000000, 30, 1, 900).first == 30);
    const double before = clock.position;
    clock.sample(2000000, 30, .5, 900);
    assert(clock.position == before);
    assert(clock.sample(4000000, 30, .5, 900).first == 60);
    clock.sample(5000000, 30, 2, 900);
    assert(clock.position == 75); // The preceding second used the old speed.
    assert(clock.sample(6000000, 30, 2, 900).first == 135);
    assert(background.sample(6000000, 30, 1, 900).first == 150);

    clock.reset(0, 899, .5);
    auto frame = clock.sample(100000, 30, .5, 900);
    assert(frame.first == 0 && frame.second == 1 && frame.alpha == 128);
    assert(clock.position == 900.5);
    clock.reset(0, 899, .5);
    frame = clock.sample(33333, 30, .5, 900);
    assert(frame.first == 899 && frame.second == 0 && frame.alpha == 127);

    // Many period changes never restart the movie or introduce a negative jump.
    clock.reset(0, 123, 1);
    for (int step = 1; step <= 100000; ++step) {
        double position = clock.position;
        frame = clock.sample(int64_t(step) * 33333, 30, speed(1, step % 791 + 10), 900);
        assert(clock.position >= position);
        assert(frame.first < 900 && frame.second < 900);
    }
}
