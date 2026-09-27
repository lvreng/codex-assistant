#pragma once

#include "lvgl.h"
#include "pet_protocol.h"

namespace CelestialDevice {
void dismiss_menu();
void init(lv_obj_t *screen, lv_obj_t *detail_page);
void cycle_theme();
Pet::Theme theme();
bool adaptive();
bool active();
void tick(lv_obj_t *stage, bool home);
void receive(const uint8_t *payload, unsigned size);
void set_model(uint8_t model);
void test_models(bool start);
void set_state(Pet::State state);
void set_api_mode(uint8_t mode);
void set_rotation_mode(uint8_t mode);
void set_astra_period(uint8_t style, uint16_t minimum);
void poll(int64_t now);
} // namespace CelestialDevice
