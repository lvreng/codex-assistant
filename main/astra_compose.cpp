#include "astra_compose.h"
#include "astra_timing.h"
#include "esp_attr.h"
#include "esp_timer.h"
#include <atomic>
#include <cstdio>
#include <cstring>

extern "C" void astra_add_p4(uint16_t *, const uint16_t *, size_t);
extern "C" void astra_mix_add_p4(uint16_t *, const uint16_t *, const uint16_t *, size_t, unsigned);

namespace AstraCompose {
namespace {
bool verified = false;
std::atomic<uint32_t> compared_pixels{0};

void accelerated(uint16_t *background, const uint16_t *a, const uint16_t *b,
                 size_t pixels, uint8_t alpha)
{
    // Vector loads force alignment, so unaligned callers must use the reference.
    if ((uintptr_t(background) | uintptr_t(a) | uintptr_t(b)) & 15) {
        AstraTiming::compose(background, a, b, pixels, alpha);
        return;
    }
    const size_t batch = pixels & ~size_t(7);
    const unsigned weight = (unsigned(alpha)*32 + 127)/255;
    if (batch) {
        if (!weight || a == b || weight == 32)
            astra_add_p4(background, weight == 32 ? b : a, batch);
        else
            astra_mix_add_p4(background, a, b, batch, weight);
    }
    AstraTiming::compose(background+batch, a+batch, b+batch, pixels-batch, alpha);
}
}

void init()
{
    // Internal test buffers keep the startup comparison small and deterministic.
    alignas(16) static DRAM_ATTR uint16_t reference[520], actual[520], a[520], b[520];
    uint32_t random = 73;
    uint32_t compared = 0;
    const auto started = esp_timer_get_time();
    for (unsigned alpha = 0; alpha < 256; ++alpha) {
        for (size_t i = 0; i < 520; ++i) {
            auto next = [&]() { random = random*1664525U+1013904223U; return uint16_t(random >> 16); };
            reference[i] = actual[i] = next();
            a[i] = next();
            b[i] = next();
            if (i % 8 == 0) a[i] = b[i] = 0;
            if (i % 8 == 1) a[i] = b[i] = 0xFFFF;
            if ((i / 8) % 5 == 0) a[i] = b[i] = 0;
            if ((i / 8) % 5 == 1) a[i] = 0;
            if ((i / 8) % 5 == 2) b[i] = 0;
        }
        const size_t count = 512 + alpha % 8;
        const auto *second = alpha % 7 ? b : a;
        AstraTiming::compose(reference, a, second, count, alpha);
        accelerated(actual, a, second, count, alpha);
        if (std::memcmp(reference, actual, sizeof(actual))) {
            std::printf("@ASTRA_COMPOSE checked=0 backend=scalar alpha=%u\n", alpha);
            for (size_t i = 0, shown = 0; i < 520 && shown < 8; ++i) {
                if (reference[i] == actual[i]) continue;
                std::printf("@ASTRA_MISMATCH index=%u a=%04x b=%04x expected=%04x actual=%04x\n",
                            unsigned(i), a[i], second[i], reference[i], actual[i]);
                ++shown;
            }
            return;
        }
        compared += count;
    }
    verified = true;
    compared_pixels.store(compared);
    std::printf("@ASTRA_COMPOSE checked=1 backend=pie pixels=%lu elapsed_us=%lld\n",
                static_cast<unsigned long>(compared),
                static_cast<long long>(esp_timer_get_time()-started));
}

uint32_t verified_pixels() { return compared_pixels.load(); }

void compose(uint16_t *background, const uint16_t *a, const uint16_t *b,
             size_t pixels, uint8_t alpha)
{
    if (verified) accelerated(background, a, b, pixels, alpha);
    else AstraTiming::compose(background, a, b, pixels, alpha);
}
}
