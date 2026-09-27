#include "lvgl.h"
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <vector>

static void flush(lv_display_t *display, const lv_area_t *, uint8_t *)
{
    lv_display_flush_ready(display);
}

int main()
{
    lv_init();
    std::vector<uint16_t> pixels(800 * 480);
    auto *display = lv_display_create(480, 800);
    lv_display_set_color_format(display, LV_COLOR_FORMAT_RGB565);
    lv_display_set_flush_cb(display, flush);

    // Reproduce the failure: changing rotation does not resize a DIRECT stride.
    lv_display_set_buffers(display, pixels.data(), nullptr, pixels.size() * 2,
                           LV_DISPLAY_RENDER_MODE_DIRECT);
    lv_display_set_rotation(display, LV_DISPLAY_ROTATION_90);
    assert(lv_display_get_buf_active(display)->header.stride == 960);

    // Production must size its buffer AFTER setting the landscape orientation.
    lv_display_set_buffers(display, pixels.data(), nullptr, pixels.size() * 2,
                           LV_DISPLAY_RENDER_MODE_DIRECT);
    assert(lv_display_get_horizontal_resolution(display) == 800);
    assert(lv_display_get_vertical_resolution(display) == 480);
    assert(lv_display_get_buf_active(display)->header.stride == 1600);
    auto *screen = lv_display_get_screen_active(display);
    lv_obj_remove_style_all(screen);
    lv_obj_set_style_bg_color(screen, lv_color_hex(0x0000ff), 0);
    lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, 0);
    auto *half = lv_obj_create(screen);
    lv_obj_remove_style_all(half);
    lv_obj_set_pos(half, 400, 0);
    lv_obj_set_size(half, 400, 480);
    lv_obj_set_style_bg_color(half, lv_color_hex(0xff0000), 0);
    lv_obj_set_style_bg_opa(half, LV_OPA_COVER, 0);
    lv_refr_now(display);
    for (int y = 0; y < 480; ++y)
        for (int x = 0; x < 800; ++x)
            assert(pixels[y * 800 + x] == (x < 400 ? 0x001f : 0xf800));

    // A partial redraw must not damage adjacent rows or undamaged regions.
    lv_obj_set_style_bg_color(half, lv_color_hex(0x00ff00), 0);
    lv_refr_now(display);
    for (int y = 0; y < 480; ++y)
        for (int x = 0; x < 800; ++x)
            assert(pixels[y * 800 + x] == (x < 400 ? 0x001f : 0x07e0));
    lv_display_delete(display);
    std::puts("landscape DIRECT stride and 768000 full/partial redraw pixels verified");
}
