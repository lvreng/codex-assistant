#pragma once

#include "lvgl.h"
#include "pet_protocol.h"

namespace Character {
bool supports(Pet::Theme theme);
lv_obj_t *create(lv_obj_t *parent);
void select(Pet::Theme theme);
void animate(Pet::State state, float dt, float transition_seconds, float touch);
} // namespace Character
