#include "celestial_device.h"
#include "device_link.h"
#include "art_receiver.h"
#include "driver/jpeg_decode.h"
#include "esp_lvgl_port.h"
#include "esp_timer.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "sd_media_player.h"
#include "model_test.h"
#include "astra_timing.h"
#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cstdlib>

LV_FONT_DECLARE(font_menu_18);

namespace CelestialDevice {
namespace {
struct Settings {
    uint32_t version = 1;
    uint16_t theme = 8;
    uint16_t style = 1;
    uint16_t minimum[2] = {50, 100};
    uint16_t maximum[2] = {75, 150};
};
Settings settings;
portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
uint16_t generation = 1;
uint8_t preview = 0;
uint8_t current_model = 0;
Thinker::ModelTest model_test;
Pet::State current_state = Pet::Offline;
int64_t last_frame = 0, save_due = 0;
std::atomic<bool> visible{false};
std::atomic<bool> ready{false};
nvs_handle_t storage = 0;
lv_obj_t *area, *image, *menu, *menu_button, *minimum_label, *maximum_label;
lv_obj_t *model_buttons[4], *style_buttons[2];
lv_obj_t *api_dropdown = nullptr, *rotation_dropdown = nullptr;
std::atomic<int> requested_api_mode{-1};
std::atomic<int> requested_rotation_mode{-1};
std::atomic<int> requested_astra_period{-1};
uint8_t shown_api_mode = 255;
uint8_t shown_rotation_mode = 1;
bool menu_open = false;
jpeg_decoder_handle_t decoder = nullptr;
uint8_t *compressed = nullptr, *pixels[2] = {};
size_t pixel_bytes[2] = {};
lv_image_dsc_t pictures[2] = {};
int front = 0;
Artwork::Assembly assembly;
uint32_t frames = 0, bad = 0;
uint64_t decode_us = 0;

Settings snapshot(uint16_t *gen = nullptr, uint8_t *key = nullptr)
{
    portENTER_CRITICAL(&mux);
    Settings result = settings;
    if (gen) *gen = generation;
    if (key) {
        const auto test = model_test.model(esp_timer_get_time());
        *key = test ? test : preview;
    }
    portEXIT_CRITICAL(&mux);
    return result;
}

void changed(bool persist)
{
    portENTER_CRITICAL(&mux);
    ++generation;
    portEXIT_CRITICAL(&mux);
    if (persist) save_due = esp_timer_get_time() + 1000000;
}

void refresh_controls()
{
    const Settings s = snapshot();
    lv_label_set_text_fmt(minimum_label, "%.1f s", s.minimum[s.style] / 10.0);
    lv_label_set_text_fmt(maximum_label, "%.1f s", s.maximum[s.style] / 10.0);
    for (int i = 0; i < 4; ++i)
        lv_obj_set_style_bg_color(model_buttons[i],
            lv_color_hex(preview == i + 1 ? 0x236B80 : 0x1A2937), 0);
    for (int i = 0; i < 2; ++i)
        lv_obj_set_style_bg_color(style_buttons[i],
            lv_color_hex(s.style == i ? 0x236B80 : 0x1A2937), 0);
}

void slide(void *obj, int32_t x) { lv_obj_set_x(static_cast<lv_obj_t *>(obj), x); }

void show_menu(bool open)
{
    menu_open = open;
    portENTER_CRITICAL(&mux);
    model_test.stop();
    if (open) {
        preview = 4;
    } else {
        preview = 0;
    }
    portEXIT_CRITICAL(&mux);
    changed(!open);
    refresh_controls();
    lv_anim_t anim;
    lv_anim_init(&anim);
    lv_anim_set_var(&anim, menu);
    lv_anim_set_exec_cb(&anim, slide);
    lv_anim_set_values(&anim, lv_obj_get_x(menu), open ? 556 : 800);
    lv_anim_set_duration(&anim, 280);
    lv_anim_set_path_cb(&anim, lv_anim_path_ease_out);
    lv_anim_start(&anim);
}

void event(lv_event_t *e)
{
    const intptr_t action = reinterpret_cast<intptr_t>(lv_event_get_user_data(e));
    if (action == 0) { show_menu(!menu_open); return; }
    if (action >= 1 && action <= 4) {
        portENTER_CRITICAL(&mux);
        preview = action;
        portEXIT_CRITICAL(&mux);
        changed(false);
    } else if (action == 12) {
        portENTER_CRITICAL(&mux);
        settings.theme = 8;
        preview = 0;
        portEXIT_CRITICAL(&mux);
        changed(true);
    } else if (action == 10 || action == 11) {
        portENTER_CRITICAL(&mux);
        settings.style = action - 10;
        preview = 4;
        portEXIT_CRITICAL(&mux);
        changed(true);
    } else if (action >= 20 && action <= 23) {
        portENTER_CRITICAL(&mux);
        const int i = settings.style;
        const int delta = (action % 2) ? 5 : -5;
        const auto periods = action < 22
            ? AstraTiming::from_minimum(int(settings.minimum[i]) + delta)
            : AstraTiming::from_maximum(int(settings.maximum[i]) + delta);
        settings.minimum[i] = periods.minimum;
        settings.maximum[i] = periods.maximum;
        preview = 4;
        portEXIT_CRITICAL(&mux);
        changed(true);
    }
    refresh_controls();
}

lv_obj_t *label(lv_obj_t *parent, const char *text, int x, int y)
{
    lv_obj_t *obj = lv_label_create(parent);
    lv_obj_set_style_text_font(obj, &font_menu_18, 0);
    lv_obj_set_style_text_color(obj, lv_color_hex(0xEDF3F9), 0);
    lv_label_set_text(obj, text);
    lv_obj_set_pos(obj, x, y);
    return obj;
}

lv_obj_t *button(lv_obj_t *parent, const char *text, int x, int y, int w, int action)
{
    lv_obj_t *obj = lv_button_create(parent);
    lv_obj_set_pos(obj, x, y);
    lv_obj_set_size(obj, w, 38);
    lv_obj_set_style_radius(obj, 6, 0);
    lv_obj_set_style_shadow_width(obj, 0, 0);
    lv_obj_set_style_pad_all(obj, 0, 0);
    lv_obj_set_style_bg_color(obj, lv_color_hex(0x1A2937), 0);
    lv_obj_t *text_obj = label(obj, text, 0, 0);
    if (action == 0 || action >= 20)
        lv_obj_set_style_text_font(text_obj, &lv_font_montserrat_18, 0);
    lv_obj_center(text_obj);
    lv_obj_add_event_cb(obj, event, LV_EVENT_CLICKED, reinterpret_cast<void *>(intptr_t(action)));
    if (action >= 20)
        lv_obj_add_event_cb(obj, event, LV_EVENT_LONG_PRESSED_REPEAT,
                            reinterpret_cast<void *>(intptr_t(action)));
    return obj;
}
} // namespace

void init(lv_obj_t *screen, lv_obj_t *detail_page)
{
    SdMediaPlayer::init();
    // Never erase existing NVS to recover settings. Defaults remain usable.
    if (nvs_flash_init() == ESP_OK &&
        nvs_open("celestial", NVS_READWRITE, &storage) == ESP_OK) {
        Settings saved;
        size_t length = sizeof(saved);
        if (nvs_get_blob(storage, "settings", &saved, &length) == ESP_OK &&
            length == sizeof(saved) && saved.version == 1 &&
            saved.theme >= 2 && saved.theme <= 8 && saved.style <= 1) {
            bool valid = true;
            for (int i = 0; i < 2; ++i)
                valid &= saved.minimum[i] >= 10 && saved.maximum[i] <= 1200 &&
                         saved.minimum[i] <= saved.maximum[i];
            if (valid) settings = saved;
        }
    }
    for (int i = 0; i < 2; ++i) {
        const auto periods = AstraTiming::from_minimum(settings.minimum[i]);
        settings.minimum[i] = periods.minimum;
        settings.maximum[i] = periods.maximum;
    }
    jpeg_decode_engine_cfg_t engine = {};
    engine.timeout_ms = 100;
    if (jpeg_new_decoder_engine(&engine, &decoder) == ESP_OK) {
    jpeg_decode_memory_alloc_cfg_t alloc = {JPEG_DEC_ALLOC_INPUT_BUFFER};
        size_t bytes = 0;
        compressed = static_cast<uint8_t *>(
            jpeg_alloc_decoder_mem(Artwork::Capacity, &alloc, &bytes));
        alloc.buffer_direction = JPEG_DEC_ALLOC_OUTPUT_BUFFER;
        for (int i = 0; i < 2; ++i) {
            pixels[i] = static_cast<uint8_t *>(jpeg_alloc_decoder_mem(
                Artwork::Width * Artwork::Height * 2, &alloc, &pixel_bytes[i]));
            pictures[i].header.magic = LV_IMAGE_HEADER_MAGIC;
            pictures[i].header.cf = LV_COLOR_FORMAT_RGB565;
            pictures[i].header.w = Artwork::Width;
            pictures[i].header.h = Artwork::Height;
            pictures[i].header.stride = Artwork::Width * 2;
            pictures[i].data_size = Artwork::Width * Artwork::Height * 2;
            pictures[i].data = pixels[i];
        }
    }
    SdMediaPlayer::init();
    area = lv_obj_create(screen);
    lv_obj_remove_style_all(area);
    lv_obj_set_pos(area, 0, 64);
    lv_obj_set_size(area, 556, 416);
    lv_obj_remove_flag(area, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(area, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_flag(area, LV_OBJ_FLAG_HIDDEN);
    image = lv_image_create(area);
    lv_obj_move_to_index(area, lv_obj_get_index(detail_page));

    menu = lv_obj_create(screen);
    lv_obj_remove_style_all(menu);
    lv_obj_set_pos(menu, 800, 64);
    lv_obj_set_size(menu, 244, 416);
    lv_obj_set_style_bg_color(menu, lv_color_hex(0x101D29), 0);
    lv_obj_set_style_bg_opa(menu, LV_OPA_COVER, 0);
    lv_obj_remove_flag(menu, LV_OBJ_FLAG_SCROLLABLE);
    label(menu, "模型动效", 16, 14);
    const char *names[] = {"Sol", "Terra", "Luna", "Astra"};
    for (int i = 0; i < 4; ++i)
        model_buttons[i] = button(menu, names[i], 16 + (i % 2) * 110,
                                 48 + (i / 2) * 46, 102, i + 1);
    label(menu, "Astra", 16, 145);
    style_buttons[0] = button(menu, "Classic", 16, 175, 102, 10);
    style_buttons[1] = button(menu, "Linked", 126, 175, 102, 11);
    label(menu, "最短周期", 16, 226);
    minimum_label = label(menu, "", 16, 254);
    button(menu, "-", 136, 232, 40, 20);
    button(menu, "+", 188, 232, 40, 21);
    label(menu, "最长周期", 16, 294);
    maximum_label = label(menu, "", 16, 322);
    button(menu, "-", 136, 300, 40, 22);
    button(menu, "+", 188, 300, 40, 23);
    button(menu, LV_SYMBOL_CLOSE, 188, 366, 40, 0);
    button(menu, "思想者", 16, 366, 102, 12);
    // Source controls precede existing effects; all effects remain reachable.
    lv_obj_update_layout(menu);
    const unsigned count = lv_obj_get_child_count(menu);
    for (unsigned i = 0; i < count; ++i) {
        lv_obj_t *child = lv_obj_get_child(menu, i);
        lv_obj_set_y(child, lv_obj_get_y(child) + 176);
    }
    lv_obj_add_flag(menu, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scroll_dir(menu, LV_DIR_VER);
    lv_obj_set_scrollbar_mode(menu, LV_SCROLLBAR_MODE_AUTO);
    label(menu, "API 来源", 16, 10);
    button(menu, LV_SYMBOL_CLOSE, 188, 4, 40, 0);
    api_dropdown = lv_dropdown_create(menu);
    lv_obj_set_pos(api_dropdown, 16, 44);
    lv_obj_set_size(api_dropdown, 212, 38);
    lv_obj_set_style_text_font(api_dropdown, &font_menu_18, 0);
    lv_obj_set_style_text_font(api_dropdown, &lv_font_montserrat_18, LV_PART_INDICATOR);
    lv_obj_set_style_bg_color(api_dropdown, lv_color_hex(0x1A2937), 0);
    lv_obj_set_style_text_color(api_dropdown, lv_color_hex(0xE9F6FB), 0);
    lv_dropdown_set_options(api_dropdown, "自动\nOpenAI\nMoreCode");
    lv_obj_set_style_text_font(lv_dropdown_get_list(api_dropdown), &font_menu_18, 0);
    lv_obj_add_event_cb(api_dropdown, [](lv_event_t *) {
        requested_api_mode = lv_dropdown_get_selected(api_dropdown);
    }, LV_EVENT_VALUE_CHANGED, nullptr);
    label(menu, "对话滚动", 16, 96);
    rotation_dropdown = lv_dropdown_create(menu);
    lv_obj_set_pos(rotation_dropdown, 16, 130);
    lv_obj_set_size(rotation_dropdown, 212, 38);
    lv_obj_set_style_text_font(rotation_dropdown, &font_menu_18, 0);
    lv_obj_set_style_text_font(rotation_dropdown, &lv_font_montserrat_18, LV_PART_INDICATOR);
    lv_obj_set_style_bg_color(rotation_dropdown, lv_color_hex(0x1A2937), 0);
    lv_obj_set_style_text_color(rotation_dropdown, lv_color_hex(0xE9F6FB), 0);
    lv_dropdown_set_options(rotation_dropdown, "关闭滚动\n只滚动执行任务\n滚动全部");
    lv_dropdown_set_selected(rotation_dropdown, shown_rotation_mode);
    lv_obj_set_style_text_font(lv_dropdown_get_list(rotation_dropdown), &font_menu_18, 0);
    lv_obj_add_event_cb(rotation_dropdown, [](lv_event_t *) {
        requested_rotation_mode = lv_dropdown_get_selected(rotation_dropdown);
    }, LV_EVENT_VALUE_CHANGED, nullptr);
    menu_button = button(screen, LV_SYMBOL_LIST, 624, 13, 38, 0);
    refresh_controls();
}

void cycle_theme()
{
    if (menu_open) show_menu(false);
    portENTER_CRITICAL(&mux);
    model_test.stop();
    const uint16_t before = settings.theme;
    // Local theme order: Beach..VanGogh, then the independent Adaptive theme.
    // Dark/Light remain legacy host themes and are intentionally skipped.
    settings.theme = (settings.theme >= static_cast<uint16_t>(Pet::Adaptive))
        ? static_cast<uint16_t>(Pet::Beach)
        : static_cast<uint16_t>(settings.theme + 1);
    preview = 0;
    portEXIT_CRITICAL(&mux);
    DeviceLink::printf("@THEME_LOCAL before=%u after=%u adaptive=%u\n",
                before, settings.theme, settings.theme == 8 ? 1 : 0);
    // Keep the currently presented layer alive until the first frame for the
    // new mode has been decoded. Clearing these flags here caused a blank
    // frame during adaptive-theme changes and exposed the LVGL swap boundary.
    changed(true);
}

Pet::Theme theme()
{
    uint8_t key;
    const Settings s = snapshot(nullptr, &key);
    // Adaptive is a real theme. The artwork layer is allowed to fall back
    // independently while disconnected or before the first frame arrives.
    return (s.theme == 8 || key) ? Pet::Adaptive
                                 : static_cast<Pet::Theme>(s.theme);
}

bool adaptive()
{
    uint8_t key;
    const Settings s = snapshot(nullptr, &key);
    return s.theme == 8 || key != 0;
}

bool active() { return visible.load(); }

void set_api_mode(uint8_t mode)
{
    if (mode <= 2 && mode != shown_api_mode && api_dropdown) {
        shown_api_mode = mode;
        lv_dropdown_set_selected(api_dropdown, mode);
    }
}

void set_rotation_mode(uint8_t mode)
{
    if (mode <= 2 && mode != shown_rotation_mode && rotation_dropdown) {
        shown_rotation_mode = mode;
        lv_dropdown_set_selected(rotation_dropdown, mode);
    }
}

void set_astra_period(uint8_t style, uint16_t minimum)
{
    if (style <= 1 && minimum >= 10 && minimum <= 800)
        requested_astra_period = (int(style) << 16) | minimum;
}

void tick(lv_obj_t *stage, bool home)
{
    const int period_request = requested_astra_period.exchange(-1);
    if (period_request >= 0) {
        const auto periods = AstraTiming::from_minimum(period_request & 0xFFFF);
        portENTER_CRITICAL(&mux);
        settings.style = period_request >> 16;
        settings.minimum[settings.style] = periods.minimum;
        settings.maximum[settings.style] = periods.maximum;
        portEXIT_CRITICAL(&mux);
        changed(true);
        refresh_controls();
    }
    int64_t last;
    uint8_t model;
    uint8_t style;
    portENTER_CRITICAL(&mux);
    last = last_frame;
    const uint8_t test = model_test.model(esp_timer_get_time());
    const bool adaptive = settings.theme == 8 || preview || test;
    model = test ? test : (preview ? preview : (current_model ? current_model : 1));
    style = settings.style;
    portEXIT_CRITICAL(&mux);
    const bool wanted = adaptive && model != 0;
    SdMediaPlayer::set_model(model);
    SdMediaPlayer::set_style(style);
    SdMediaPlayer::set_enabled(wanted);
    if (wanted && pixels[0] && pixels[1]) {
        const int back = 1 - front;
        uint32_t sequence = 0;
        if (SdMediaPlayer::take_frame(pixels[back], pixel_bytes[back], &sequence)) {
            (void)sequence;
            lv_image_cache_drop(&pictures[back]);
            pictures[back].data = pixels[back];
            lv_image_set_src(image, &pictures[back]);
            lv_obj_invalidate(image);
            front = back;
            last = esp_timer_get_time();
            portENTER_CRITICAL(&mux);
            last_frame = last;
            portEXIT_CRITICAL(&mux);
            ready = true;
        }
    }
    const int64_t now = esp_timer_get_time();
    // Adaptive is SD-only. Once the card/read task fails, do not keep showing
    // a stale decoded frame or fall back to the character layer.
    const bool frame_valid = wanted && ready && SdMediaPlayer::using_media() &&
                             now - last < 5000000;
    const bool area_visible = frame_valid;
    // Adaptive has no character-layer fallback. Its only visual source is
    // the local SD clip, so a missing card cannot look like local playback.
    const bool stage_visible = !adaptive;
    visible = area_visible;

    // Do not remove the old layer while an adaptive frame is being decoded.
    // The new image is installed first, then both visibility flags are changed
    // in the same LVGL critical section so the renderer never sees a blank
    // intermediate frame.
    if (lvgl_port_lock(50)) {
        if (lv_obj_has_flag(area, LV_OBJ_FLAG_HIDDEN) == area_visible)
            lv_obj_update_flag(area, LV_OBJ_FLAG_HIDDEN, !area_visible);
        if (lv_obj_has_flag(stage, LV_OBJ_FLAG_HIDDEN) == stage_visible)
            lv_obj_update_flag(stage, LV_OBJ_FLAG_HIDDEN, !stage_visible);
        lvgl_port_unlock();
    }
    if (lv_obj_has_flag(menu_button, LV_OBJ_FLAG_HIDDEN) == home)
        lv_obj_update_flag(menu_button, LV_OBJ_FLAG_HIDDEN, !home);
    if (save_due && esp_timer_get_time() >= save_due) {
        save_due = 0;
        const Settings s = snapshot();
        if (storage && nvs_set_blob(storage, "settings", &s, sizeof(s)) == ESP_OK)
            nvs_commit(storage);
    }
}

void dismiss_menu()
{
    if (menu_open) show_menu(false);
}

void receive(const uint8_t *payload, unsigned size)
{
    // Adaptive artwork is intentionally local-only. USB art packets are never
    // allowed to become a hidden fallback for the SD player.
    if (adaptive()) {
        ++bad;
        const unsigned frame = size >= 4 ? Artwork::u16(payload + 2) : 0;
        DeviceLink::printf("@ART_ACK %u rejected_local_only\n", frame);
        return;
    }
    uint16_t gen;
    snapshot(&gen);
    const auto result = assembly.feed(payload, size, gen, compressed);
    if (result == Artwork::Assembly::Partial) return;
    const unsigned frame = size >= 4 ? Artwork::u16(payload + 2) : 0;
    if (result == Artwork::Assembly::Clear) {
        ready = false;
        DeviceLink::printf("@ART_ACK %u clear\n", frame);
        return;
    }
    if (result != Artwork::Assembly::Complete) {
        if (result != Artwork::Assembly::Stale) ++bad;
        if (size >= 14 && uint64_t(Artwork::u32(payload + 6)) + size - 14 ==
                          Artwork::u32(payload + 10))
            DeviceLink::printf("@ART_ACK %u rejected\n", frame);
        return;
    }
    const int back = 1 - front;
    jpeg_decode_picture_info_t info = {};
    uint32_t bytes = 0;
    const int64_t started = esp_timer_get_time();
    jpeg_decode_cfg_t config = {};
    config.output_format = JPEG_DECODE_OUT_FORMAT_RGB565;
    // IDF's BGR order is little-endian RGB565, as expected by LVGL.
    config.rgb_order = JPEG_DEC_RGB_ELEMENT_ORDER_BGR;
    const bool decoded = decoder && pixels[back] &&
        jpeg_decoder_get_info(compressed, assembly.size, &info) == ESP_OK &&
        info.width == Artwork::Width && info.height == Artwork::Height &&
        jpeg_decoder_process(decoder, &config, compressed, assembly.size,
            pixels[back], pixel_bytes[back], &bytes) == ESP_OK &&
        bytes == Artwork::Width * Artwork::Height * 2;
    const char *status = "decode_error";
    if (decoded && lvgl_port_lock(100)) {
        uint16_t current;
        snapshot(&current);
        status = "dropped";
        if (current == gen) {
            lv_image_cache_drop(&pictures[back]);
            lv_image_set_src(image, &pictures[back]);
            lv_obj_invalidate(image);
            front = back;
            portENTER_CRITICAL(&mux);
            last_frame = esp_timer_get_time();
            portEXIT_CRITICAL(&mux);
            ready = true;
            ++frames;
            decode_us += esp_timer_get_time() - started;
            status = "ok";
        }
        lvgl_port_unlock();
    } else {
        ++bad;
        if (decoded) status = "dropped";
    }
    DeviceLink::printf("@ART_ACK %u %s\n", frame, status);
}

void set_model(uint8_t model)
{
    portENTER_CRITICAL(&mux);
    current_model = model <= 4 ? model : 0;
    const auto test = model_test.model(esp_timer_get_time());
    const auto selected = test ? test : (preview ? preview : current_model);
    portEXIT_CRITICAL(&mux);
    SdMediaPlayer::set_model(selected);
}

void test_models(bool start)
{
    if (start && !SdMediaPlayer::storage_ready()) {
        DeviceLink::printf("@MODEL_TEST_ERROR no_sd\n");
        return;
    }
    portENTER_CRITICAL(&mux);
    if (start) model_test.start(esp_timer_get_time());
    else model_test.stop();
    portEXIT_CRITICAL(&mux);
    changed(false);
}

void set_state(Pet::State state)
{
    portENTER_CRITICAL(&mux);
    current_state = state;
    const bool testing = preview || model_test.model(esp_timer_get_time()) != 0;
    portEXIT_CRITICAL(&mux);
    SdMediaPlayer::set_view(testing ? Pet::Thinking : state);
}

void poll(int64_t now)
{
    const int api_mode = requested_api_mode.exchange(-1);
    if (api_mode >= 0 && api_mode <= 2) {
        const char *sources[] = {"auto", "official", "morecode"};
        DeviceLink::printf("@API_SOURCE %s\n", sources[api_mode]);
    }
    const int rotation_mode = requested_rotation_mode.exchange(-1);
    if (rotation_mode >= 0 && rotation_mode <= 2) {
        const char *modes[] = {"off", "active", "all"};
        DeviceLink::printf("@ROTATE_MODE %s\n", modes[rotation_mode]);
    }
    static uint16_t sent = 0;
    static int64_t requested = 0, reported = 0;
    static bool sent_media_status = false;
    static bool media_status_initialized = false;
    static uint32_t last_local_report = 0;
    static uint8_t last_test = 255;
    static int64_t test_reported = 0;
    uint16_t gen;
    uint8_t key;
    const Settings s = snapshot(&gen, &key);
    portENTER_CRITICAL(&mux);
    if (!SdMediaPlayer::storage_ready()) model_test.stop();
    const auto test = model_test.model(now);
    const auto state = current_state;
    portEXIT_CRITICAL(&mux);
    if (last_test != test || now-test_reported >= 1000000) {
        DeviceLink::printf("@MODEL_TEST active=%u step=%u model=%u\n", test ? 1 : 0, test, test);
        last_test = test;
        test_reported = now;
    }
    SdMediaPlayer::set_view(key || test || state == Pet::Offline ? Pet::Thinking : state);
    SdMediaPlayer::set_style(s.style);
    for (uint8_t i = 0; i < 2; ++i)
        SdMediaPlayer::set_astra_period(i, s.minimum[i]);
    SdMediaPlayer::set_model(key ? key : (current_model ? current_model : 1));
    SdMediaPlayer::poll(now);
    const bool media_active = SdMediaPlayer::using_media();
    if (!media_status_initialized || media_active != sent_media_status) {
        DeviceLink::printf("@SD_MEDIA %u\n", media_active ? 1 : 0);
        sent_media_status = media_active;
        media_status_initialized = true;
    }
    if (!media_active && (sent != gen || now - requested >= 2000000)) {
        DeviceLink::printf("@ART_REQUEST %u %u %u %u %u %u\n", gen, s.theme, key,
                    s.style, s.minimum[s.style], s.maximum[s.style]);
        sent = gen;
        requested = now;
    }
    if (now - reported >= 5000000) {
        uint32_t transition_frames, transition_max_us;
        SdMediaPlayer::transition_stats(&transition_frames, &transition_max_us);
        const uint32_t local_total = SdMediaPlayer::frames_decoded();
        const uint32_t local_sequence = SdMediaPlayer::frame_sequence();
        DeviceLink::printf("@ART_STAT fps=%.1f frames=%lu decode_ms=%.1f bad=%lu active=%u theme=%u "
                    "local_frames=%lu local_seq=%lu local_delta=%lu uptime_s=%.1f "
                    "transition_frames=%lu transition_max_ms=%.2f local_model=%u\n",
                    frames * 1000000.0 / (now - reported),
                    static_cast<unsigned long>(frames),
                    frames ? decode_us / (1000.0 * frames) : 0.0,
                    static_cast<unsigned long>(bad), unsigned(active()), s.theme,
                    static_cast<unsigned long>(local_total),
                    static_cast<unsigned long>(local_sequence),
                    static_cast<unsigned long>(local_total - last_local_report),
                    now / 1000000.0,
                    static_cast<unsigned long>(transition_frames), transition_max_us / 1000.0,
                    SdMediaPlayer::frame_model());
        last_local_report = local_total;
        frames = 0;
        decode_us = 0;
        reported = now;
    }
}
} // namespace CelestialDevice
