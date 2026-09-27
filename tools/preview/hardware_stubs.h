#pragma once
#include <cstdint>

using portMUX_TYPE = int;
constexpr int portMUX_INITIALIZER_UNLOCKED = 0;
inline int64_t preview_time_us = 1000000;
inline void portENTER_CRITICAL(portMUX_TYPE *) {}
inline void portEXIT_CRITICAL(portMUX_TYPE *) {}
inline int64_t esp_timer_get_time() { return preview_time_us; }
inline void board_display_set_brightness(int) {}
