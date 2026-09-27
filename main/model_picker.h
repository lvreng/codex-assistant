#pragma once
#include <cstddef>
#include <cstdint>
#include "lvgl.h"

namespace ModelPicker {
constexpr unsigned PayloadBytes = 58 + 224 * 24 / 8;
struct Command {
    const char *action;
    uint32_t context;
    uint16_t request;
    uint8_t key;
};
bool take_command(Command &command);
void init(lv_obj_t *screen);
void button(bool pressed);
void receive(const uint8_t *payload, size_t size);
bool tick(uint32_t now, bool connected, uint32_t background, uint32_t foreground,
          uint32_t muted, uint32_t accent);
bool visible();
}
