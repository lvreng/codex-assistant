#pragma once

#include <cstddef>
#include <cstdint>

#include "pet_protocol.h"

namespace SdMediaPlayer {

struct UploadResult {
    bool handled = false;
    bool ok = false;
    uint32_t offset = 0;
    uint32_t crc = 0;
    bool complete = false;
    char reason[40] = {};
};

bool init();
void set_model(uint8_t model);
void set_view(Pet::State state);
void set_style(uint8_t style);
void set_astra_period(uint8_t style, uint16_t minimum);
void set_enabled(bool value);
bool available();
bool storage_ready();
bool using_media();
uint32_t frame_sequence();
uint32_t frames_decoded();
uint8_t frame_model();
void transition_stats(uint32_t *count, uint32_t *maximum_us);
UploadResult upload(const uint8_t *payload, unsigned size);
// Exchange a retired, JPEG-allocated display buffer for the decoded frame.
// Caller must hold the LVGL lock; its currently displayed buffer is never passed.
bool take_frame(uint8_t *&destination, size_t &capacity, uint32_t *sequence);
bool active();
void poll(int64_t now);

} // namespace SdMediaPlayer
