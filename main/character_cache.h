#pragma once

#include "lvgl.h"

namespace Character {
// Fixed-size material poses, prepared once before animation starts.
void prepare_materials(lv_obj_t *parent);
const lv_image_dsc_t *material_pose(const lv_image_dsc_t &source,
                                   float width, float height, float radians);
const lv_image_dsc_t *eyelid_pose(const lv_image_dsc_t &source,
                                  float opening, float *shown_opening = nullptr);
} // namespace Character
