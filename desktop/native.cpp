#include <array>
#include <cstring>
#include "../main/thinker_transition.h"

// Same UI, assets, protocol and gestures as the device; no ESP entry points.
#include "../main/main.cpp"

namespace {
std::array<uint16_t, 800 * 480> desktop_pixels{};
std::array<uint16_t, 800 * 100> desktop_draw_buffer{};
lv_indev_t *desktop_pointer = nullptr;
lv_point_t desktop_position{};
lv_indev_state_t desktop_press = LV_INDEV_STATE_RELEASED;
uint64_t desktop_generation = 0;
bool desktop_initialized = false;

void desktop_flush(lv_display_t *display, const lv_area_t *area, uint8_t *data)
{
    const auto *source = reinterpret_cast<const uint16_t *>(data);
    const size_t width = area->x2 - area->x1 + 1;
    for (int y = area->y1; y <= area->y2; ++y) {
        std::memcpy(desktop_pixels.data() + y * 800 + area->x1, source, width * 2);
        source += width;
    }
    ++desktop_generation;
    lv_display_flush_ready(display);
}
}

extern "C" {
void pet_thinker_camera(int from,int to,float progress,float *values)
{
    const auto c=Thinker::camera(from,to,progress);
    values[0]=c.position.x; values[1]=c.position.y; values[2]=c.position.z;
    values[3]=c.focal; values[4]=c.yaw; values[5]=c.pitch;
    for (int model=1;model<=3;++model) {
        const auto point=Thinker::project(c,Thinker::body_position(model));
        values[model*3+3]=point.x; values[model*3+4]=point.y; values[model*3+5]=point.z;
    }
    // Two aligned points at different depths expose actual parallax to tests.
    for (int i=0;i<2;++i) {
        const float depth=i?12000:2000;
        const auto point=Thinker::project(c,{72*(depth+16000)/440,-36*(depth+16000)/440,depth});
        values[15+i*3]=point.x; values[16+i*3]=point.y; values[17+i*3]=point.z;
    }
}

void pet_thinker_render(const void *a, const void *b, void *output, int width, int height,
                        int from, int to, float progress, int bits)
{
    if (bits == 16)
        Thinker::render565(static_cast<const uint16_t *>(a),static_cast<const uint16_t *>(b),
                          static_cast<uint16_t *>(output),width,height,from,to,progress);
    else if (bits == 32)
        Thinker::render888(static_cast<const uint32_t *>(a),static_cast<const uint32_t *>(b),
                          static_cast<uint32_t *>(output),width,height,from,to,progress);
}

const uint16_t *pet_init()
{
    if (desktop_initialized) return desktop_pixels.data();
    desktop_initialized = true;
    lv_init();
    auto *display = lv_display_create(800, 480);
    lv_display_set_color_format(display, LV_COLOR_FORMAT_RGB565);
    lv_display_set_buffers(display, desktop_draw_buffer.data(), nullptr,
                          desktop_draw_buffer.size() * 2, LV_DISPLAY_RENDER_MODE_PARTIAL);
    lv_display_set_flush_cb(display, desktop_flush);
    shared_view.input_tps_x10 = shared_view.output_tps_x10 = UINT32_MAX;
    create_ui();
    desktop_pointer = lv_indev_create();
    lv_indev_set_type(desktop_pointer, LV_INDEV_TYPE_POINTER);
    lv_indev_set_mode(desktop_pointer, LV_INDEV_MODE_EVENT);
    lv_indev_set_display(desktop_pointer, display);
    lv_indev_set_read_cb(desktop_pointer, [](lv_indev_t *, lv_indev_data_t *data) {
        data->point = desktop_position;
        data->state = desktop_press;
    });
    lv_refr_now(display);
    return desktop_pixels.data();
}

uint64_t pet_tick(uint32_t elapsed_ms)
{
    preview_time_us += static_cast<int64_t>(elapsed_ms) * 1000;
    lv_tick_inc(elapsed_ms);
    if (connected && preview_time_us - last_packet_us > 5000000) {
        auto view = get_view();
        view.state = Pet::Offline;
        view.total = view.index = view.pending = 0;
        view.input_tps_x10 = view.output_tps_x10 = UINT32_MAX;
        view.usage_stale = 1;
        set_view(view, false);
    }
    lv_timer_handler();
    return desktop_generation;
}

int pet_feed(const uint8_t *bytes, size_t size)
{
    int accepted = 0;
    for (size_t i = 0; i < size; ++i) {
        if (!receiver.feed(bytes[i])) continue;
        if (receiver.is_view()) {
            Pet::View view;
            receiver.copy_view(view);
            set_view(view, true);
            last_packet_us = preview_time_us;
            ++accepted;
        }
    }
    return accepted;
}

void pet_pointer(int x, int y, int pressed)
{
    desktop_position = {std::clamp(x, 0, 799), std::clamp(y, 0, 479)};
    desktop_press = pressed ? LV_INDEV_STATE_PRESSED : LV_INDEV_STATE_RELEASED;
    lv_indev_read(desktop_pointer);
}

void pet_theme(int theme)
{
    if (theme >= 0 && theme < Pet::ThemeCount)
        activate_local_theme(static_cast<Pet::Theme>(theme));
    else if (theme == -1)
        local_theme_override = false;
}

void pet_cycle_theme() { request_theme_cycle(); }
void pet_brightness(int value) { set_brightness(value); }
int pet_navigation() { return consume_session_step(); }
void pet_details(int open)
{
    detail_target = open != 0;
    if (detail_target) {
        lv_obj_remove_flag(ui.detail_page, LV_OBJ_FLAG_HIDDEN);
        lv_obj_move_foreground(ui.gesture_layer);
    }
}

// Lightweight diagnostics for the shell and offline integration tests.
int pet_value(int field)
{
    switch (field) {
    case 0: return shown_connected;
    case 1: return shown_theme;
    case 2: return shown_state;
    case 3: return get_view().total;
    case 4: return get_view().index;
    case 5: return detail_target;
    case 6: return brightness;
    case 7: return receiver.good;
    case 8: return receiver.bad;
    case 9: return lv_obj_has_flag(ui.detail_page, LV_OBJ_FLAG_HIDDEN)
                       ? 800 : lv_obj_get_x(ui.detail_page);
    case 10: return get_view().api_mode;
    case 11: return get_view().api_source;
    case 12: return get_view().week_remaining_x10;
    case 13: return lv_arc_get_value(ui.quota_ring);
    default: return -1;
    }
}

const char *pet_account_text(int field)
{
    switch (field) {
    case 0: return lv_label_get_text(ui.api_caption);
    case 1: return lv_label_get_text(ui.quota_percent);
    case 2: return lv_label_get_text(ui.account_plan);
    case 3: return lv_label_get_text(ui.account_expiry);
    case 4: return lv_label_get_text(ui.account_reset);
    case 5: return lv_label_get_text(ui.detail_model_title);
    case 6: {
        static char result[17];
        const auto view = get_view();
        std::memcpy(result, view.membership_expires_at, sizeof(result));
        return result;
    }
    default: return "";
    }
}
}
