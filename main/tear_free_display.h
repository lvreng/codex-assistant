#pragma once
#include "esp_lcd_panel_ops.h"
#include "lvgl.h"

namespace TearFreeDisplay {
lv_display_t *create(esp_lcd_panel_handle_t panel, int native_width, int native_height);
void report();
}
