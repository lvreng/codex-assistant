#pragma once

#include <cstddef>
#include <cstdint>

namespace AstraCompose {
// Called once on the playback task before publishing any accelerated frame.
void init();
uint32_t verified_pixels();
void compose(uint16_t *background, const uint16_t *a, const uint16_t *b,
             size_t pixels, uint8_t alpha);
}
