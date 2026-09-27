#pragma once

#include "esp_err.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_touch.h"
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    esp_lcd_panel_io_handle_t lcd_io;
    esp_lcd_panel_handle_t panel;
    esp_lcd_touch_handle_t touch;
} board_display_handles_t;

esp_err_t board_display_init(board_display_handles_t *handles);
esp_err_t board_display_set_brightness(int percent);
esp_err_t board_theme_button_init(void);
bool board_theme_button_pressed(void);

#ifdef __cplusplus
}
#endif
