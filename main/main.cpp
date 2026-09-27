#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <cstdint>
#include <unistd.h>
#include "device_link.h"

#ifdef PET_DESKTOP_PREVIEW
#include "../tools/preview/hardware_stubs.h"
#else
#include "board_display.h"
#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "esp_chip_info.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_lvgl_port.h"
#include "esp_lcd_mipi_dsi.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "celestial_device.h"
#include "sd_media_player.h"
#include "tear_free_display.h"
#endif
#include "lvgl.h"
#include "generated/vangogh_scene.h"
#include "pet_protocol.h"
#include "theme_character.h"
#include "model_picker.h"

LV_FONT_DECLARE(font_cn_18);
LV_FONT_DECLARE(font_cn_30);
LV_FONT_DECLARE(font_pixel_16);
LV_FONT_DECLARE(font_pixel_24);
LV_FONT_DECLARE(font_menu_18);

namespace {

constexpr int NativeWidth = 480;
constexpr int NativeHeight = 800;
constexpr int ScreenWidth = 800;
constexpr int ScreenHeight = 480;
constexpr int DefaultBrightness = 86;
constexpr float Pi = 3.14159265358979323846f;
const char *Tag = "codex_pet";

const char *StateNames[Pet::StateCount] = {
    "待机", "思考中", "执行中", "等待确认", "已完成",
    "出错", "已暂停", "未连接", "查资料",
};
const char *StateDetails[Pet::StateCount] = {
    "随时准备开始新的任务",
    "正在理解上下文并规划",
    "正在编辑、运行和验证",
    "需要你的选择",
    "最新任务顺利完成",
    "有一项需要检查",
    "工作暂时休息",
    "等待电脑桥接服务",
    "正在扫描文件与参考",
};
const char *StateGlyphs[Pet::StateCount] = {
    LV_SYMBOL_PLAY, "...", LV_SYMBOL_SETTINGS, "?", LV_SYMBOL_OK,
    "!", LV_SYMBOL_PAUSE, "Z", LV_SYMBOL_EYE_OPEN,
};
const uint32_t StateColors[Pet::StateCount] = {
    0x39D8C5, 0x5AB7FF, 0xA98AFF, 0xFFC857, 0x4DDB93,
    0xFF6B6B, 0x8EA2BE, 0x66778C, 0x35C7E8,
};

struct Ui {
    lv_obj_t *screen = nullptr;
    lv_obj_t *header_title = nullptr;
    lv_obj_t *session_title = nullptr;
    lv_obj_t *next_session_title = nullptr;
    lv_obj_t *connection = nullptr;
    lv_obj_t *connection_dot = nullptr;
    lv_obj_t *connection_text = nullptr;
    lv_obj_t *top_rule = nullptr;
    lv_obj_t *beach_sky = nullptr;
    lv_obj_t *beach_horizon = nullptr;
    lv_obj_t *beach_sand = nullptr;
    lv_obj_t *pixel_top_strip[6] = {};
    lv_obj_t *pixel_grid_v[8] = {};
    lv_obj_t *pixel_grid_h[5] = {};
    lv_obj_t *pixel_corner[8] = {};
    lv_obj_t *pixel_scanline = nullptr;
    lv_obj_t *pixel_ear_left = nullptr;
    lv_obj_t *pixel_ear_right = nullptr;
    lv_obj_t *pixel_brow_left[3] = {};
    lv_obj_t *pixel_brow_right[3] = {};
    lv_obj_t *pixel_mouth[5] = {};
    lv_obj_t *pixel_meter[8] = {};
    lv_obj_t *vangogh_scene = nullptr;
    lv_obj_t *vangogh_moon = nullptr;
    lv_obj_t *vangogh_star[12] = {};
    lv_obj_t *vangogh_window[6] = {};
    lv_obj_t *vangogh_swirl[8] = {};
    lv_obj_t *vangogh_tree[4] = {};
    lv_obj_t *vangogh_flow[4] = {};
    lv_obj_t *stage = nullptr;
    lv_obj_t *character = nullptr;
    lv_obj_t *aura_outer = nullptr;
    lv_obj_t *aura_inner = nullptr;
    lv_obj_t *face = nullptr;
    lv_obj_t *face_inner = nullptr;
    lv_obj_t *eye_glow_left = nullptr;
    lv_obj_t *eye_glow_right = nullptr;
    lv_obj_t *eye_left = nullptr;
    lv_obj_t *eye_right = nullptr;
    lv_obj_t *pupil_left = nullptr;
    lv_obj_t *pupil_right = nullptr;
    lv_obj_t *glint_left = nullptr;
    lv_obj_t *glint_right = nullptr;
    lv_obj_t *brow_left = nullptr;
    lv_obj_t *brow_right = nullptr;
    lv_obj_t *mouth = nullptr;
    lv_obj_t *cheek_left = nullptr;
    lv_obj_t *cheek_right = nullptr;
    lv_obj_t *effect = nullptr;
    lv_obj_t *particles[6] = {};
    lv_obj_t *side_rule = nullptr;
    lv_obj_t *metric_rule = nullptr;
    lv_obj_t *state_dot = nullptr;
    lv_obj_t *state_caption = nullptr;
    lv_obj_t *models_caption = nullptr;
    lv_obj_t *state_name = nullptr;
    lv_obj_t *state_detail = nullptr;
    lv_obj_t *session_duration = nullptr;
    lv_obj_t *api_caption = nullptr;
    lv_obj_t *api_hint = nullptr;
    lv_obj_t *balance_value = nullptr;
    lv_obj_t *quota_ring = nullptr;
    lv_obj_t *quota_percent = nullptr;
    lv_obj_t *account_plan = nullptr;
    lv_obj_t *account_expiry = nullptr;
    lv_obj_t *account_reset = nullptr;
    lv_obj_t *model_name[Pet::ModelCount] = {};
    lv_obj_t *model_cost[Pet::ModelCount] = {};
    lv_obj_t *input_rate = nullptr;
    lv_obj_t *input_arrow = nullptr;
    lv_obj_t *output_rate = nullptr;
    lv_obj_t *output_arrow = nullptr;
    lv_obj_t *data_panel = nullptr;
    lv_obj_t *detail_page = nullptr;
    lv_obj_t *detail_back = nullptr;
    lv_obj_t *detail_freshness = nullptr;
    lv_obj_t *detail_rule = nullptr;
    lv_obj_t *detail_cards[5] = {};
    lv_obj_t *detail_values[5] = {};
    lv_obj_t *detail_titles[5] = {};
    lv_obj_t *detail_subtitle = nullptr;
    lv_obj_t *detail_title = nullptr;
    lv_obj_t *detail_chart_title = nullptr;
    lv_obj_t *detail_model_title = nullptr;
    lv_obj_t *detail_chart = nullptr;
    lv_chart_series_t *input_series = nullptr;
    lv_chart_series_t *output_series = nullptr;
    lv_obj_t *detail_input_rate = nullptr;
    lv_obj_t *detail_output_rate = nullptr;
    lv_obj_t *detail_model_name[Pet::ModelCount] = {};
    lv_obj_t *detail_model_cost[Pet::ModelCount] = {};
    lv_obj_t *detail_model_tokens[Pet::ModelCount] = {};
    lv_obj_t *detail_model_track[Pet::ModelCount] = {};
    lv_obj_t *detail_model_fill[Pet::ModelCount] = {};
    lv_obj_t *gesture_layer = nullptr;
    lv_obj_t *text_objects[96] = {};
    const lv_font_t *text_fonts[96] = {};
    uint8_t text_pixel_size[96] = {};
    int text_count = 0;
    lv_obj_t *main_labels[48] = {};
    lv_obj_t *muted_labels[64] = {};
    int main_count = 0;
    int muted_count = 0;
};

struct FacePose {
    float eye_open = 1.0f;
    float eye_width = 84.0f;
    float eye_height = 68.0f;
    float gaze_x = 0.0f;
    float gaze_y = 0.0f;
    float brow_tilt = 0.0f;
    float brow_y = 0.0f;
    float mouth_curve = 9.0f;
    float mouth_width = 76.0f;
    float mouth_y = 199.0f;
    float cheek = 0.35f;
};

struct ThemePalette {
    uint32_t bg;
    uint32_t stage;
    uint32_t shell;
    uint32_t face;
    uint32_t main;
    uint32_t muted;
    uint32_t rule;
    uint32_t panel;
    uint32_t track;
    uint32_t eye;
    uint32_t pupil;
    uint32_t glint;
    uint32_t input;
    uint32_t output;
};

Ui ui;
Pet::View shared_view;
portMUX_TYPE view_mux = portMUX_INITIALIZER_UNLOCKED;
bool connected = false;
int64_t last_packet_us = 0;
int brightness = DefaultBrightness;
Pet::Theme local_theme = Pet::Dark;
Pet::State shown_state = Pet::Offline;
Pet::Theme shown_theme = Pet::Dark;
bool shown_connected = false;
uint32_t shown_balance_quota = UINT32_MAX;
uint32_t shown_spent_quota = UINT32_MAX;
uint32_t shown_request_count = UINT32_MAX;
uint32_t shown_today_request_count = UINT32_MAX;
uint32_t shown_today_quota = UINT32_MAX;
uint64_t shown_today_prompt_tokens = UINT64_MAX;
uint64_t shown_today_completion_tokens = UINT64_MAX;
uint32_t shown_input_tps_x10 = UINT32_MAX;
uint32_t shown_output_tps_x10 = UINT32_MAX;
uint32_t shown_quota_per_unit = 500000;
uint8_t shown_usage_stale = 1;
uint32_t shown_account_hash = UINT32_MAX;
uint8_t shown_api_source = 2;
uint32_t shown_duration_seconds = UINT32_MAX;
uint32_t shown_models_hash = UINT32_MAX;
uint32_t shown_labels_hash = UINT32_MAX;
lv_point_t press_point{};
int press_brightness = DefaultBrightness;
bool long_press_handled = false;
float touch_energy = 0.0f;
bool local_theme_override = false;
bool theme_cycle_pending = false;
bool detail_target = false;
float detail_progress = 0.0f;
uint32_t frame_count = 0;
int64_t stat_started_us = 0;
int64_t last_anim_us = 0;
int64_t last_chart_sample_us = 0;
int32_t chart_range_x10 = 100;
FacePose animated_pose;
float accent_r = 102.0f;
float accent_g = 119.0f;
float accent_b = 140.0f;
float beach_shape = 0.0f;
float vangogh_moon_progress = 0.0f;
float vangogh_state_energy = 0.0f;
int vangogh_animation_frame = -1;
int pixel_feature_frame = -1;
int pixel_particle_frame = -1;
Pet::State pixel_feature_state = Pet::StateCount;
Pet::State pixel_particle_state = Pet::StateCount;
int session_step_pending = 0;
bool title_transition_active = false;
float title_transition_progress = 1.0f;
alignas(4) uint8_t title_transition_labels[Pet::LabelBytes] = {};
alignas(4) uint16_t session_title_buffer[Pet::TextWidth * 24] = {};
alignas(4) uint16_t next_session_title_buffer[Pet::TextWidth * 24] = {};
uint16_t *current_title_buffer = session_title_buffer;
uint16_t *next_title_buffer = next_session_title_buffer;
lv_point_precise_t brow_left_points[3];
lv_point_precise_t brow_right_points[3];
lv_point_precise_t mouth_points[5];
lv_point_precise_t vangogh_swirl_points[8][10];
lv_point_precise_t vangogh_tree_points[4][7];
lv_point_precise_t vangogh_flow_points[4][6];
Pet::Receiver receiver;

constexpr ThemePalette DarkPalette = {
    0x071019, 0x0A1722, 0x172837, 0x0D1B28, 0xEDF7FA, 0x8498A8,
    0x203342, 0x10202C, 0x1C303F, 0xE9FBFF, 0x173444, 0xFFFFFF,
    0x39D8C5, 0xFF8A78,
};
constexpr ThemePalette LightPalette = {
    0xF4F7F8, 0xE8EFF1, 0xD4E1E5, 0xF8FBFC, 0x162630, 0x667983,
    0xCCD8DC, 0xE2E9EB, 0xD9E2E5, 0x183846, 0xF8FCFD, 0x8DE8E0,
    0x278C9A, 0xDE715F,
};
constexpr ThemePalette BeachPalette = {
    0xDFF5E8, 0xF8FDFC, 0xB9E5E1, 0xFAFFFC, 0x192126, 0x67747D,
    0x87B9B7, 0xF8FDFC, 0xD5E9E5, 0x155B66, 0xF2C94C, 0xFFFFFF,
    0x278C9A, 0xE4A928,
};
constexpr ThemePalette PixelPalette = {
    0x09091D, 0x111138, 0x6C4BFF, 0x171735, 0xF7F7FF, 0x76DDF2,
    0x3B3B72, 0x10102B, 0x282852, 0xFFF36D, 0x24244E, 0xFFFFFF,
    0x43F5B5, 0xFF5FA2,
};
constexpr ThemePalette MechanicalPalette = {
    0xE3EAED, 0xEEF3F4, 0xD7DFE4, 0xEEF2F4, 0x19252F, 0x65757F,
    0xCBD5DB, 0xE7EEF0, 0xD5E0E4, 0xFFFFFF, 0x18232B, 0xFFFFFF,
    0x168B78, 0xDB4C38,
};
constexpr ThemePalette PaperPalette = {
    0xFFF1E7, 0xFFF8F1, 0xFF735C, 0xFF9477, 0x202C43, 0x687489,
    0xEBDCD3, 0xFFF2E8, 0xF2E1D8, 0xFFFAE7, 0x194A94, 0xFFFFFF,
    0x275AAF, 0xD75244,
};
constexpr ThemePalette GlassPalette = {
    0xDFF6F3, 0xEFFCFB, 0x86DFDB, 0xB9F2ED, 0x122E39, 0x607D86,
    0xC9DFDD, 0xE3F7F3, 0xD9EEE9, 0xFFFFFF, 0x132D3C, 0xFFFFFF,
    0x00876D, 0x037DA7,
};
constexpr ThemePalette VanGoghPalette = {
    0x07162D, 0x0A1B35, 0x173F69, 0x1C4A73, 0xF1D57A, 0xA9B8B5,
    0x8A7037, 0x0D294C, 0x183D63, 0x264D72, 0xE3C356, 0xFFF1A6,
    0x79C4CF, 0xD7AF4F,
};
constexpr uint32_t VanGoghStateColors[Pet::StateCount] = {
    0xF4C95D, 0x5ED9D4, 0xF4A259, 0xFFE8A3, 0xFFD166,
    0xF2766B, 0x91A9D3, 0x55779E, 0x6EDCF2,
};

const ThemePalette &theme_palette(Pet::Theme theme)
{
    switch (theme) {
    case Pet::Light:
        return LightPalette;
    case Pet::Beach:
        return BeachPalette;
    case Pet::Pixel:
        return PixelPalette;
    case Pet::Mechanical:
        return MechanicalPalette;
    case Pet::Paper:
        return PaperPalette;
    case Pet::Glass:
        return GlassPalette;
    case Pet::VanGogh:
        return VanGoghPalette;
    case Pet::Adaptive:
        // Adaptive artwork is rendered from SD with a deep-space background.
        // Do not inherit the pale Glass palette around the 560x416 frame.
        return DarkPalette;
    default:
        return DarkPalette;
    }
}

lv_color_t color(uint32_t rgb)
{
    return lv_color_hex(rgb);
}

uint32_t mix_rgb(uint32_t a, uint32_t b, float amount)
{
    amount = std::clamp(amount, 0.0f, 1.0f);
    const float inverse = 1.0f - amount;
    const uint32_t r = static_cast<uint32_t>(((a >> 16) & 255) * inverse +
                                             ((b >> 16) & 255) * amount);
    const uint32_t g = static_cast<uint32_t>(((a >> 8) & 255) * inverse +
                                             ((b >> 8) & 255) * amount);
    const uint32_t bl = static_cast<uint32_t>((a & 255) * inverse +
                                              (b & 255) * amount);
    return (r << 16) | (g << 8) | bl;
}

void set_text_color(lv_obj_t *obj, uint32_t rgb)
{
    lv_obj_set_style_text_color(obj, color(rgb), 0);
}

lv_obj_t *make_label(lv_obj_t *parent, const char *text, const lv_font_t *font,
                     uint32_t rgb, bool pixel_text = true)
{
    lv_obj_t *label = lv_label_create(parent);
    lv_label_set_text(label, text);
    lv_obj_set_style_text_font(label, font, 0);
    set_text_color(label, rgb);
    lv_obj_set_style_text_letter_space(label, 0, 0);
    if (pixel_text && ui.text_count < 96) {
        ui.text_objects[ui.text_count] = label;
        ui.text_fonts[ui.text_count] = font;
        ui.text_pixel_size[ui.text_count] = font->line_height >= 22 ? 24 : 16;
        ++ui.text_count;
    }
    return label;
}

lv_obj_t *main_label(lv_obj_t *parent, const char *text, const lv_font_t *font)
{
    lv_obj_t *label = make_label(parent, text, font, 0xEDF7FA);
    ui.main_labels[ui.main_count++] = label;
    return label;
}

lv_obj_t *muted_label(lv_obj_t *parent, const char *text, const lv_font_t *font)
{
    lv_obj_t *label = make_label(parent, text, font, 0x8498A8);
    ui.muted_labels[ui.muted_count++] = label;
    return label;
}

void set_pixel_text_size(lv_obj_t *label, uint8_t size)
{
    for (int i = 0; i < ui.text_count; ++i) {
        if (ui.text_objects[i] == label) {
            ui.text_pixel_size[i] = size;
            return;
        }
    }
}

lv_obj_t *make_box(lv_obj_t *parent, int x, int y, int width, int height,
                   int radius, uint32_t rgb, lv_opa_t opacity = LV_OPA_COVER)
{
    lv_obj_t *obj = lv_obj_create(parent);
    lv_obj_remove_style_all(obj);
    lv_obj_set_pos(obj, x, y);
    lv_obj_set_size(obj, width, height);
    lv_obj_set_style_radius(obj, radius, 0);
    lv_obj_set_style_bg_color(obj, color(rgb), 0);
    lv_obj_set_style_bg_opa(obj, opacity, 0);
    lv_obj_clear_flag(obj, LV_OBJ_FLAG_SCROLLABLE);
    return obj;
}

void set_view(const Pet::View &view, bool is_connected)
{
    portENTER_CRITICAL(&view_mux);
    shared_view = view;
    connected = is_connected;
    portEXIT_CRITICAL(&view_mux);
}

Pet::View get_view(bool *is_connected = nullptr)
{
    Pet::View result;
    portENTER_CRITICAL(&view_mux);
    result = shared_view;
    if (is_connected) {
        *is_connected = connected;
    }
    portEXIT_CRITICAL(&view_mux);
    return result;
}

void set_brightness(int value)
{
    brightness = std::clamp(value, 12, 100);
    board_display_set_brightness(brightness);
}

Pet::Theme active_theme(Pet::Theme host_theme)
{
#ifndef PET_DESKTOP_PREVIEW
    (void)host_theme;
    // Adaptive is represented by the artwork layer, not by a wire-level
    // Theme enum. Keep a stable visual fallback while the first frame is
    // being decoded or while the host is offline.
    if (CelestialDevice::adaptive())
        return Pet::Adaptive;
    return CelestialDevice::theme();
#else
    Pet::Theme result;
    portENTER_CRITICAL(&view_mux);
    result = local_theme_override ? local_theme : host_theme;
    portEXIT_CRITICAL(&view_mux);
    return result;
#endif
}

[[maybe_unused]]
void activate_local_theme(Pet::Theme theme)
{
    portENTER_CRITICAL(&view_mux);
    local_theme = theme;
    local_theme_override = true;
    portEXIT_CRITICAL(&view_mux);
}

void request_theme_cycle()
{
    portENTER_CRITICAL(&view_mux);
    theme_cycle_pending = true;
    portEXIT_CRITICAL(&view_mux);
}

bool consume_theme_cycle()
{
    bool pending;
    portENTER_CRITICAL(&view_mux);
    pending = theme_cycle_pending;
    theme_cycle_pending = false;
    portEXIT_CRITICAL(&view_mux);
    return pending;
}

const char *theme_name(Pet::Theme theme)
{
    switch (theme) {
    case Pet::Light:
        return "light";
    case Pet::Beach:
        return "beach";
    case Pet::Pixel:
        return "pixel";
    case Pet::Mechanical:
        return "mechanical";
    case Pet::Paper:
        return "paper";
    case Pet::Glass:
        return "glass";
    case Pet::VanGogh:
        return "vangogh";
    case Pet::Adaptive:
        return "adaptive";
    default:
        return "dark";
    }
}

void apply_theme_layout(Pet::Theme theme)
{
    const bool pixel = theme == Pet::Pixel;
    const bool vangogh = theme == Pet::VanGogh;
    const bool custom = Character::supports(theme);
    lv_obj_update_flag(ui.stage, LV_OBJ_FLAG_HIDDEN, false);
    for (lv_obj_t *obj : {ui.face, ui.aura_outer, ui.aura_inner})
        lv_obj_update_flag(obj, LV_OBJ_FLAG_HIDDEN, custom || vangogh);
    lv_obj_update_flag(ui.effect, LV_OBJ_FLAG_HIDDEN, custom || vangogh);
    for (lv_obj_t *obj : ui.particles)
        lv_obj_update_flag(obj, LV_OBJ_FLAG_HIDDEN, custom || vangogh);
    lv_obj_update_flag(ui.vangogh_scene, LV_OBJ_FLAG_HIDDEN, !vangogh);
    lv_obj_update_flag(ui.vangogh_moon, LV_OBJ_FLAG_HIDDEN, !vangogh);
    for (lv_obj_t *star : ui.vangogh_star) {
        lv_obj_update_flag(star, LV_OBJ_FLAG_HIDDEN, !vangogh);
    }
    for (lv_obj_t *window : ui.vangogh_window) {
        lv_obj_update_flag(window, LV_OBJ_FLAG_HIDDEN, !vangogh);
    }
    for (lv_obj_t *swirl : ui.vangogh_swirl) {
        lv_obj_update_flag(swirl, LV_OBJ_FLAG_HIDDEN, !vangogh);
    }
    for (lv_obj_t *tree : ui.vangogh_tree) {
        lv_obj_update_flag(tree, LV_OBJ_FLAG_HIDDEN, !vangogh);
    }
    for (lv_obj_t *flow : ui.vangogh_flow) {
        lv_obj_update_flag(flow, LV_OBJ_FLAG_HIDDEN, !vangogh);
    }
    lv_obj_update_flag(ui.state_caption, LV_OBJ_FLAG_HIDDEN, custom);
    // The painted theme communicates state through the artwork itself. The
    // auxiliary line is hidden here so it cannot introduce unsupported glyphs
    // or a visual placeholder into the Starry Night composition.
    lv_obj_update_flag(ui.state_detail, LV_OBJ_FLAG_HIDDEN, custom || vangogh);
    lv_obj_update_flag(ui.api_hint, LV_OBJ_FLAG_HIDDEN, custom);
    Character::select(theme);
    lv_obj_set_pos(ui.stage, pixel || vangogh ? 14 : 20, pixel || vangogh ? 72 : 78);
    lv_obj_set_size(ui.stage, pixel || vangogh ? 538 : 520,
                    pixel || vangogh ? 366 : 350);
    lv_obj_set_pos(ui.data_panel, 566, pixel || vangogh ? 70 : 74);
    lv_obj_set_size(ui.data_panel, 218, pixel || vangogh ? 386 : 378);
    lv_obj_set_pos(ui.side_rule, 557, 78);
    lv_obj_set_size(ui.side_rule, 1, 350);
    lv_obj_set_pos(ui.state_caption, 580, vangogh ? 80 : 82);
    lv_obj_set_pos(ui.state_dot, 580, 121);
    lv_obj_set_size(ui.state_dot, 11, 11);
    lv_obj_set_style_radius(ui.state_dot, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_pos(ui.state_name, 604, 106);
    lv_obj_set_pos(ui.state_detail, 580, 154);
    lv_obj_set_pos(ui.session_duration, 580, vangogh ? 145 : 174);
    lv_obj_set_pos(ui.metric_rule, 580, vangogh ? 166 : 194);
    lv_obj_set_size(ui.metric_rule, 196, 1);
    lv_obj_set_pos(ui.api_caption, 580, vangogh ? 176 : 204);
    lv_obj_set_pos(ui.balance_value, 580, vangogh ? 199 : 227);
    lv_obj_set_width(ui.balance_value, 196);
    lv_obj_set_pos(ui.models_caption, 580, vangogh ? 240 : 270);
    lv_obj_set_style_opa(ui.side_rule, pixel ? LV_OPA_TRANSP : LV_OPA_COVER, 0);
    lv_obj_set_style_opa(ui.top_rule, pixel ? LV_OPA_TRANSP : LV_OPA_COVER, 0);
    for (unsigned i = 0; i < Pet::ModelCount; ++i) {
        const int y = pixel ? 304 + static_cast<int>(i) * 38
                            : vangogh ? 276 + static_cast<int>(i) * 38
                            : 294 + static_cast<int>(i) * 25;
        lv_obj_set_pos(ui.model_name[i], 580, y);
        lv_obj_set_pos(ui.model_cost[i], 708, y);
        lv_obj_set_width(ui.model_name[i], 124);
        lv_obj_set_width(ui.model_cost[i], 68);
        lv_obj_update_flag(ui.model_name[i], LV_OBJ_FLAG_HIDDEN, i >= 2);
        lv_obj_update_flag(ui.model_cost[i], LV_OBJ_FLAG_HIDDEN, i >= 2);
    }
    lv_obj_set_pos(ui.input_rate, 580, pixel ? 397 : vangogh ? 369 : 399);
    lv_obj_set_pos(ui.input_arrow, 756, pixel ? 397 : vangogh ? 369 : 399);
    lv_obj_set_pos(ui.output_rate, 580, pixel ? 423 : vangogh ? 395 : 424);
    lv_obj_set_pos(ui.output_arrow, 756, pixel ? 423 : vangogh ? 395 : 424);
    lv_obj_set_width(ui.input_rate, 172);
    lv_obj_set_width(ui.output_rate, 172);
    if (!custom) return;

    const bool paper = theme == Pet::Paper;
    const bool mechanical = theme == Pet::Mechanical;
    const int data_x = mechanical ? 28 : 572;
    lv_obj_set_pos(ui.stage, paper ? 0 : mechanical ? 258 : 12, paper ? 64 : 78);
    lv_obj_set_size(ui.stage, paper ? 800 : 528, paper ? 274 : 334);
    lv_obj_set_pos(ui.character, paper ? 140 : 0, paper ? -13 : -4);
    lv_obj_set_size(ui.character, paper ? 520 : 528, paper ? 292 : 342);
    lv_obj_set_pos(ui.data_panel, paper ? 20 : data_x - 8, paper ? 392 : 82);
    lv_obj_set_size(ui.data_panel, paper ? 760 : 218, paper ? 84 : 378);
    lv_obj_set_pos(ui.side_rule, paper ? 20 : mechanical ? 252 : 552, paper ? 390 : 84);
    lv_obj_set_size(ui.side_rule, paper ? 760 : 1, paper ? 1 : 372);
    lv_obj_set_pos(ui.state_name, paper ? 312 : mechanical ? 304 : 50, paper ? 344 : 422);
    lv_obj_set_pos(ui.session_duration, paper ? 453 : mechanical ? 451 : 200,
                   paper ? 353 : 431);
    lv_obj_set_pos(ui.state_dot, paper ? 292 : mechanical ? 284 : 30,
                   paper ? 352 : 431);
    lv_obj_set_size(ui.state_dot, 5, 22);
    lv_obj_set_style_radius(ui.state_dot, 1, 0);
    lv_obj_set_pos(ui.api_caption, paper ? 28 : data_x, paper ? 402 : 96);
    lv_obj_set_pos(ui.balance_value, paper ? 28 : data_x, paper ? 430 : 129);
    lv_obj_set_width(ui.balance_value, paper ? 205 : 204);
    lv_obj_set_pos(ui.models_caption, paper ? 264 : data_x, paper ? 402 : 226);
    lv_obj_set_pos(ui.metric_rule, paper ? 242 : data_x, paper ? 408 : 200);
    lv_obj_set_size(ui.metric_rule, paper ? 1 : 204, paper ? 52 : 1);
    for (unsigned i = 0; i < 2; ++i) {
        const int y = paper ? 429 + i * 25 : 270 + i * 40;
        lv_obj_set_pos(ui.model_name[i], paper ? 264 : data_x, y);
        lv_obj_set_pos(ui.model_cost[i], paper ? 438 : data_x + 124, y);
        lv_obj_set_width(ui.model_name[i], paper ? 165 : 120);
        lv_obj_set_width(ui.model_cost[i], 80);
    }
    lv_obj_set_pos(ui.input_arrow, paper ? 563 : data_x, paper ? 415 : 394);
    lv_obj_set_pos(ui.output_arrow, paper ? 563 : data_x, paper ? 446 : 427);
    lv_obj_set_pos(ui.input_rate, paper ? 590 : data_x + 26, paper ? 413 : 392);
    lv_obj_set_pos(ui.output_rate, paper ? 590 : data_x + 26, paper ? 444 : 425);
    lv_obj_set_width(ui.input_rate, 184);
    lv_obj_set_width(ui.output_rate, 184);
}

void set_pixel_object(lv_obj_t *obj, bool visible)
{
    if (obj) {
        lv_obj_set_style_opa(obj, visible ? LV_OPA_COVER : LV_OPA_TRANSP, 0);
    }
}

void apply_theme(Pet::Theme theme)
{
    shown_theme = theme;
    const ThemePalette &palette = theme_palette(theme);
    const bool beach = theme == Pet::Beach;
    const bool pixel = theme == Pet::Pixel;
    const bool vangogh = theme == Pet::VanGogh;
    const bool custom = Character::supports(theme);
    apply_theme_layout(theme);
    vangogh_animation_frame = -1;

    for (int i = 0; i < ui.text_count; ++i) {
        const lv_font_t *font = pixel
            ? (ui.text_pixel_size[i] == 24 ? &font_pixel_24 : &font_pixel_16)
            : ui.text_fonts[i];
        lv_obj_set_style_text_font(ui.text_objects[i], font, 0);
    }
    if (custom) {
        lv_obj_set_style_text_font(ui.balance_value, &lv_font_montserrat_32, 0);
        for (unsigned i = 0; i < 2; ++i) {
            lv_obj_set_style_text_font(ui.model_name[i], &lv_font_montserrat_18, 0);
            lv_obj_set_style_text_font(ui.model_cost[i], &lv_font_montserrat_18, 0);
        }
        lv_obj_set_style_text_font(ui.input_rate, &lv_font_montserrat_18, 0);
        lv_obj_set_style_text_font(ui.output_rate, &lv_font_montserrat_18, 0);
        if (theme == Pet::Paper)
            lv_obj_set_style_text_font(ui.balance_value, &lv_font_montserrat_28, 0);
    }
    for (lv_obj_t *strip : ui.pixel_top_strip) {
        set_pixel_object(strip, pixel);
    }
    for (lv_obj_t *line : ui.pixel_grid_v) {
        set_pixel_object(line, pixel);
    }
    for (lv_obj_t *line : ui.pixel_grid_h) {
        set_pixel_object(line, pixel);
    }
    for (lv_obj_t *corner : ui.pixel_corner) {
        set_pixel_object(corner, pixel);
    }
    set_pixel_object(ui.pixel_scanline, pixel);
    set_pixel_object(ui.pixel_ear_left, pixel);
    set_pixel_object(ui.pixel_ear_right, pixel);
    for (lv_obj_t *block : ui.pixel_brow_left) {
        set_pixel_object(block, pixel);
    }
    for (lv_obj_t *block : ui.pixel_brow_right) {
        set_pixel_object(block, pixel);
    }
    for (lv_obj_t *block : ui.pixel_mouth) {
        set_pixel_object(block, pixel);
    }
    for (lv_obj_t *segment : ui.pixel_meter) {
        set_pixel_object(segment, pixel);
    }
    lv_obj_set_style_opa(ui.vangogh_scene,
                         vangogh ? LV_OPA_COVER : LV_OPA_TRANSP, 0);
    lv_obj_set_style_opa(ui.vangogh_moon,
                         vangogh ? LV_OPA_COVER : LV_OPA_TRANSP, 0);
    for (lv_obj_t *star : ui.vangogh_star) {
        set_pixel_object(star, vangogh);
    }
    for (lv_obj_t *window : ui.vangogh_window) {
        set_pixel_object(window, vangogh);
    }
    for (lv_obj_t *swirl : ui.vangogh_swirl) {
        set_pixel_object(swirl, vangogh);
    }
    for (lv_obj_t *tree : ui.vangogh_tree) {
        set_pixel_object(tree, vangogh);
    }
    for (lv_obj_t *flow : ui.vangogh_flow) {
        set_pixel_object(flow, vangogh);
    }
    lv_obj_set_style_opa(ui.brow_left, pixel ? LV_OPA_TRANSP : LV_OPA_COVER, 0);
    lv_obj_set_style_opa(ui.brow_right, pixel ? LV_OPA_TRANSP : LV_OPA_COVER, 0);
    lv_obj_set_style_opa(ui.mouth, pixel ? LV_OPA_TRANSP : LV_OPA_COVER, 0);
    lv_obj_set_style_opa(ui.cheek_left, pixel ? LV_OPA_TRANSP : LV_OPA_COVER, 0);
    lv_obj_set_style_opa(ui.cheek_right, pixel ? LV_OPA_TRANSP : LV_OPA_COVER, 0);
    lv_obj_set_style_opa(ui.eye_glow_left, pixel ? LV_OPA_TRANSP : LV_OPA_COVER, 0);
    lv_obj_set_style_opa(ui.eye_glow_right, pixel ? LV_OPA_TRANSP : LV_OPA_COVER, 0);

    lv_obj_set_style_bg_color(ui.screen, color(palette.bg), 0);
    lv_obj_set_style_bg_color(ui.stage, color(palette.stage), 0);
    lv_obj_set_style_border_width(ui.stage, pixel ? 3 : vangogh ? 2 : 0, 0);
    lv_obj_set_style_border_color(
        ui.stage, color(pixel ? 0x3B3B72 : palette.rule), 0);
    lv_obj_set_style_radius(ui.stage, pixel ? 2 : 8, 0);
    lv_obj_set_style_bg_color(ui.face, color(palette.shell), 0);
    lv_obj_set_style_bg_color(ui.face_inner, color(palette.face), 0);
    lv_obj_set_style_bg_color(ui.connection, color(palette.panel), 0);
    lv_obj_set_style_border_color(ui.connection, color(palette.rule), 0);
    lv_obj_set_style_radius(ui.connection, pixel ? 2 : 8, 0);
    lv_obj_set_style_border_width(ui.connection, pixel ? 2 : 1, 0);
    for (lv_obj_t *rule_obj : {ui.top_rule, ui.side_rule, ui.metric_rule, ui.detail_rule}) {
        lv_obj_set_style_bg_color(rule_obj, color(palette.rule), 0);
    }
    lv_obj_set_style_bg_color(ui.data_panel, color(palette.panel), 0);
    lv_obj_set_style_border_color(ui.data_panel, color(palette.rule), 0);
    lv_obj_set_style_radius(ui.data_panel, pixel ? 2 : 8, 0);
    lv_obj_set_style_border_width(ui.data_panel, custom ? 0 : pixel ? 2 : 1, 0);
    lv_obj_set_style_outline_width(ui.data_panel, pixel ? 2 : 0, 0);
    lv_obj_set_style_outline_color(
        ui.data_panel, color(pixel ? 0x6C4BFF : palette.rule), 0);
    lv_obj_set_style_outline_pad(ui.data_panel, pixel ? 3 : 0, 0);
    lv_obj_set_style_radius(ui.stage, pixel ? 2 : 8, 0);
    lv_obj_set_style_border_width(ui.stage, pixel ? 3 : vangogh ? 2 : 0, 0);
    lv_obj_set_style_border_color(
        ui.stage, color(pixel ? 0x3B3B72 : palette.rule), 0);
    for (lv_obj_t *scene : {ui.beach_sky, ui.beach_horizon, ui.beach_sand}) {
        lv_obj_set_style_opa(scene, beach ? LV_OPA_COVER : LV_OPA_TRANSP, 0);
    }
    lv_obj_set_style_bg_color(ui.beach_sky, color(0xFFF8D9), 0);
    lv_obj_set_style_bg_color(ui.beach_horizon, color(0x9FE7E4), 0);
    lv_obj_set_style_bg_color(ui.beach_sand, color(0xF2C94C), 0);
    for (lv_obj_t *window : ui.vangogh_window) {
        lv_obj_set_style_bg_color(window, color(0xF3C95D), 0);
        lv_obj_set_style_radius(window, 0, 0);
    }
    for (lv_obj_t *swirl : ui.vangogh_swirl) {
        lv_obj_set_style_line_rounded(swirl, true, 0);
        lv_obj_set_style_line_width(swirl, 2, 0);
        lv_obj_set_style_line_color(swirl, color(0x4F89A0), 0);
    }
    for (lv_obj_t *tree : ui.vangogh_tree) {
        lv_obj_set_style_line_rounded(tree, true, 0);
        lv_obj_set_style_line_width(tree, 2, 0);
    }
    for (int i = 0; i < 4; ++i) {
        lv_obj_set_style_line_rounded(ui.vangogh_flow[i], true, 0);
        lv_obj_set_style_line_width(ui.vangogh_flow[i], i < 2 ? 2 : 1, 0);
    }
    lv_obj_set_style_bg_opa(ui.face,
                            vangogh ? LV_OPA_TRANSP : LV_OPA_COVER, 0);
    lv_obj_set_style_bg_opa(ui.face_inner,
                            vangogh ? LV_OPA_TRANSP : LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(ui.face, pixel ? 4 : vangogh ? 0 : 2, 0);
    lv_obj_set_style_border_color(
        ui.face, color(beach ? 0x2C4348 :
                       pixel ? 0x43F5B5 :
                       vangogh ? VanGoghStateColors[shown_state] :
                       StateColors[shown_state]), 0);
    lv_obj_set_style_border_opa(
        ui.face, vangogh ? LV_OPA_TRANSP :
                 beach ? LV_OPA_100 : LV_OPA_COVER, 0);
    lv_obj_set_style_radius(ui.face, pixel ? 6 : beach ? 82 : 64, 0);
    lv_obj_set_style_radius(ui.face_inner, pixel ? 3 : beach ? 72 : 56, 0);
    lv_obj_set_style_radius(ui.aura_outer, pixel ? 0 : beach ? 90 : 78, 0);
    lv_obj_set_style_radius(ui.aura_inner, pixel ? 0 : beach ? 80 : 70, 0);
    lv_obj_set_style_radius(ui.face, pixel ? 0 : beach ? 82 : 64, 0);
    lv_obj_set_style_radius(ui.face_inner, pixel ? 0 : beach ? 72 : 56, 0);
    lv_obj_set_style_outline_width(ui.face, pixel ? 3 : 0, 0);
    lv_obj_set_style_outline_color(ui.face, color(pixel ? 0x09091D : palette.rule), 0);
    lv_obj_set_style_outline_pad(ui.face, pixel ? 4 : 0, 0);
    for (lv_obj_t *eye : {ui.eye_left, ui.eye_right}) {
        lv_obj_set_style_radius(eye, pixel ? 2 : LV_RADIUS_CIRCLE, 0);
        lv_obj_set_style_border_width(eye, vangogh ? 2 : 0, 0);
        lv_obj_set_style_border_color(eye, color(0xB89B48), 0);
        lv_obj_set_style_bg_opa(eye, vangogh ? LV_OPA_80 : LV_OPA_COVER, 0);
    }
    for (lv_obj_t *glow : {ui.eye_glow_left, ui.eye_glow_right}) {
        lv_obj_set_style_bg_opa(glow, LV_OPA_TRANSP, 0);
        lv_obj_set_style_border_width(glow, vangogh ? 2 : 0, 0);
        lv_obj_set_style_border_color(glow, color(0x8CAAA6), 0);
        lv_obj_set_style_radius(glow, LV_RADIUS_CIRCLE, 0);
    }
    for (lv_obj_t *pupil : {ui.pupil_left, ui.pupil_right}) {
        lv_obj_set_style_radius(pupil, pixel ? 1 : LV_RADIUS_CIRCLE, 0);
    }
    for (lv_obj_t *glint : {ui.glint_left, ui.glint_right}) {
        lv_obj_set_style_radius(glint, pixel ? 0 : LV_RADIUS_CIRCLE, 0);
    }
    for (lv_obj_t *cheek : {ui.cheek_left, ui.cheek_right}) {
        lv_obj_set_style_radius(cheek, pixel ? 0 : LV_RADIUS_CIRCLE, 0);
    }
    for (lv_obj_t *particle : ui.particles) {
        lv_obj_set_style_radius(particle, pixel ? 0 : LV_RADIUS_CIRCLE, 0);
    }
    if (pixel) {
        constexpr uint32_t PixelColors[6] = {
            0x43F5B5, 0x4DDCFF, 0x6C4BFF, 0xFF5FA2, 0xFFF36D, 0xFF8A4D,
        };
        for (int i = 0; i < 6; ++i) {
            lv_obj_set_style_bg_color(ui.pixel_top_strip[i], color(PixelColors[i]), 0);
        }
        lv_obj_set_style_bg_color(ui.pixel_ear_left, color(0x6C4BFF), 0);
        lv_obj_set_style_bg_color(ui.pixel_ear_right, color(0x6C4BFF), 0);
    }
    lv_obj_set_style_line_rounded(ui.brow_left, !pixel, 0);
    lv_obj_set_style_line_rounded(ui.brow_right, !pixel, 0);
    lv_obj_set_style_line_rounded(ui.mouth, !pixel, 0);
    lv_obj_set_style_line_width(ui.brow_left, pixel ? 7 : vangogh ? 4 : 5, 0);
    lv_obj_set_style_line_width(ui.brow_right, pixel ? 7 : vangogh ? 4 : 5, 0);
    lv_obj_set_style_line_width(ui.mouth, pixel ? 8 : vangogh ? 4 : 6, 0);
    if (ui.detail_page) {
        lv_obj_set_style_bg_color(ui.detail_page, color(palette.bg), 0);
        lv_obj_set_style_bg_color(ui.detail_chart, color(palette.panel), 0);
        lv_obj_set_style_border_color(ui.detail_chart, color(palette.rule), 0);
        lv_obj_set_style_line_color(ui.detail_chart, color(palette.rule), LV_PART_MAIN);
        for (lv_obj_t *card : ui.detail_cards) {
            lv_obj_set_style_bg_color(card, color(palette.panel), 0);
            lv_obj_set_style_border_color(card, color(palette.rule), 0);
            lv_obj_set_style_radius(card, pixel ? 2 : 8, 0);
            lv_obj_set_style_border_width(card, pixel ? 2 : 1, 0);
        }
        for (lv_obj_t *track : ui.detail_model_track) {
            lv_obj_set_style_bg_color(track, color(palette.track), 0);
        }
        for (unsigned i = 0; i < Pet::ModelCount; ++i) {
            const uint32_t model_color =
                i == 0 ? palette.input :
                i == 1 ? 0x4F9DFF :
                i == 2 ? 0x66C98A : palette.output;
            lv_obj_set_style_bg_color(ui.detail_model_fill[i], color(model_color), 0);
        }
        lv_chart_set_series_color(ui.detail_chart, ui.input_series, color(palette.input));
        lv_chart_set_series_color(ui.detail_chart, ui.output_series, color(palette.output));
    }
    for (lv_obj_t *eye : {ui.eye_left, ui.eye_right}) {
        lv_obj_set_style_bg_color(eye, color(palette.eye), 0);
    }
    for (lv_obj_t *pupil : {ui.pupil_left, ui.pupil_right}) {
        lv_obj_set_style_bg_color(pupil, color(palette.pupil), 0);
    }
    for (lv_obj_t *glint : {ui.glint_left, ui.glint_right}) {
        lv_obj_set_style_bg_color(glint, color(palette.glint), 0);
    }
    lv_obj_set_style_text_color(ui.input_rate, color(palette.input), 0);
    lv_obj_set_style_text_color(ui.input_arrow, color(palette.input), 0);
    lv_obj_set_style_text_color(ui.detail_input_rate, color(palette.input), 0);
    lv_obj_set_style_text_color(ui.output_rate, color(palette.output), 0);
    lv_obj_set_style_text_color(ui.output_arrow, color(palette.output), 0);
    lv_obj_set_style_text_color(ui.detail_output_rate, color(palette.output), 0);
    lv_obj_set_style_text_color(ui.detail_back, color(palette.input), 0);
    for (int i = 0; i < ui.main_count; ++i) {
        set_text_color(ui.main_labels[i], palette.main);
    }
    for (int i = 0; i < ui.muted_count; ++i) {
        set_text_color(ui.muted_labels[i], palette.muted);
    }
}

uint32_t model_hash(const Pet::View &view)
{
    uint32_t hash = 2166136261u;
    const uint8_t *data = reinterpret_cast<const uint8_t *>(view.models);
    for (size_t i = 0; i < sizeof(view.models); ++i) {
        hash = (hash ^ data[i]) * 16777619u;
    }
    return (hash ^ view.model_count) * 16777619u;
}

uint32_t labels_hash(const Pet::View &view)
{
    uint32_t hash = 2166136261u;
    for (uint8_t byte : view.labels) {
        hash = (hash ^ byte) * 16777619u;
    }
    return hash;
}

void paint_title_bitmap(lv_obj_t *canvas, uint16_t *buffer,
                        const uint8_t *bits, uint32_t background,
                        uint32_t foreground, bool fade)
{
    const uint16_t bg = lv_color_to_u16(color(background));
    for (int y = 0; y < 24; ++y) {
        for (int x = 0; x < static_cast<int>(Pet::TextWidth); ++x) {
            const bool set = bits[y * Pet::TextWidth / 8 + x / 8] &
                             (0x80U >> (x & 7));
            uint32_t pixel = background;
            if (set) {
                const float opacity = fade
                    ? std::max(0.18f, 0.48f - x / static_cast<float>(Pet::TextWidth) * 0.25f)
                    : 1.0f;
                pixel = mix_rgb(background, foreground, opacity);
            }
            buffer[y * Pet::TextWidth + x] =
                set ? lv_color_to_u16(color(pixel)) : bg;
        }
    }
    lv_obj_invalidate(canvas);
}

void paint_session_titles(const Pet::View &view, Pet::Theme theme)
{
    const ThemePalette &palette = theme_palette(theme);
    paint_title_bitmap(ui.session_title, current_title_buffer, view.labels,
                       palette.bg, palette.main, false);
    paint_title_bitmap(ui.next_session_title, next_title_buffer,
                       view.labels + Pet::HeadingBytes,
                       palette.bg, palette.muted, true);
    lv_obj_set_x(ui.session_title, 220);
    lv_obj_set_x(ui.next_session_title, 460);
    lv_obj_set_width(ui.next_session_title, 150);
    lv_obj_set_style_opa(ui.session_title, LV_OPA_COVER, 0);
    lv_obj_set_style_opa(ui.next_session_title, LV_OPA_100, 0);
}

void begin_title_transition(const Pet::View &view, Pet::Theme theme)
{
    const ThemePalette &palette = theme_palette(theme);
    std::memcpy(title_transition_labels, view.labels, Pet::LabelBytes);
    paint_title_bitmap(ui.next_session_title, next_title_buffer,
                       title_transition_labels, palette.bg, palette.main, false);
    lv_obj_set_width(ui.next_session_title, Pet::TextWidth);
    lv_obj_set_x(ui.session_title, 220);
    lv_obj_set_x(ui.next_session_title, 460);
    lv_obj_set_style_opa(ui.session_title, LV_OPA_COVER, 0);
    lv_obj_set_style_opa(ui.next_session_title, LV_OPA_20, 0);
    title_transition_progress = 0.0f;
    title_transition_active = true;
}

void finish_title_transition(Pet::Theme theme)
{
    const ThemePalette &palette = theme_palette(theme);
    std::swap(current_title_buffer, next_title_buffer);
    lv_canvas_set_buffer(ui.session_title, current_title_buffer,
                         Pet::TextWidth, 24, LV_COLOR_FORMAT_RGB565);
    lv_canvas_set_buffer(ui.next_session_title, next_title_buffer,
                         Pet::TextWidth, 24, LV_COLOR_FORMAT_RGB565);
    paint_title_bitmap(ui.next_session_title, next_title_buffer,
                       title_transition_labels + Pet::HeadingBytes,
                       palette.bg, palette.muted, true);
    lv_obj_set_width(ui.next_session_title, 150);
    lv_obj_set_x(ui.session_title, 220);
    lv_obj_set_x(ui.next_session_title, 460);
    lv_obj_set_style_opa(ui.session_title, LV_OPA_COVER, 0);
    lv_obj_set_style_opa(ui.next_session_title, LV_OPA_100, 0);
    title_transition_progress = 1.0f;
    title_transition_active = false;
}

void format_money(uint32_t quota, uint32_t per_unit, char *out, size_t size)
{
    if (quota == UINT32_MAX || per_unit == 0) {
        std::snprintf(out, size, "--");
    } else {
        std::snprintf(out, size, "$%.2f",
                      static_cast<double>(quota) / static_cast<double>(per_unit));
    }
}

void format_tokens(uint64_t value, char *out, size_t size)
{
    if (value == UINT64_MAX) {
        std::snprintf(out, size, "--");
    } else if (value >= 1000000) {
        std::snprintf(out, size, "%.1fM", value / 1000000.0);
    } else if (value >= 1000) {
        std::snprintf(out, size, "%.1fK", value / 1000.0);
    } else {
        std::snprintf(out, size, "%llu", static_cast<unsigned long long>(value));
    }
}

void format_rate(uint32_t rate_x10, char *out, size_t size)
{
    if (rate_x10 == UINT32_MAX) {
        std::snprintf(out, size, "-- Token/s");
    } else if (rate_x10 >= 10000000) {
        std::snprintf(out, size, "%.1fM Token/s", rate_x10 / 10000000.0);
    } else if (rate_x10 >= 10000) {
        std::snprintf(out, size, "%.1fK Token/s", rate_x10 / 10000.0);
    } else {
        std::snprintf(out, size, "%.1f Token/s", rate_x10 / 10.0);
    }
}

void format_duration(uint32_t seconds, char *out, size_t size)
{
    const uint32_t hours = seconds / 3600;
    const uint32_t minutes = (seconds / 60) % 60;
    const uint32_t secs = seconds % 60;
    std::snprintf(out, size, "TIME %02lu:%02lu:%02lu",
                  static_cast<unsigned long>(hours),
                  static_cast<unsigned long>(minutes),
                  static_cast<unsigned long>(secs));
}

FacePose pose_for(Pet::State state, float t)
{
    FacePose pose;
    const float soft = std::sin(t * 1.7f);
    const float pulse = (std::sin(t * 3.4f) + 1.0f) * 0.5f;
    pose.gaze_x = std::sin(t * 0.72f) * 2.2f;
    pose.gaze_y = std::sin(t * 0.91f) * 1.2f;
    switch (state) {
    case Pet::Idle:
        pose.eye_open = 0.92f + soft * 0.06f;
        pose.gaze_x = std::sin(t * 0.55f) * 5.0f;
        pose.gaze_y = std::sin(t * 0.82f) * 2.0f;
        pose.brow_y = soft * 1.5f;
        pose.mouth_curve = 9.0f + pulse * 4.0f;
        pose.mouth_width = 72.0f + soft * 4.0f;
        pose.cheek = 0.28f + pulse * 0.12f;
        break;
    case Pet::Thinking:
        pose.eye_open = 0.72f + pulse * 0.15f;
        pose.gaze_x = std::sin(t * 1.05f) * 11.0f;
        pose.gaze_y = -9.0f + std::cos(t * 2.1f) * 2.0f;
        pose.brow_tilt = -6.0f + soft * 2.0f;
        pose.brow_y = -3.0f;
        pose.mouth_curve = 1.0f + soft * 2.0f;
        pose.mouth_width = 48.0f + pulse * 8.0f;
        pose.cheek = 0.10f + pulse * 0.08f;
        break;
    case Pet::Working:
        pose.eye_open = 0.62f + pulse * 0.20f;
        pose.gaze_x = std::sin(t * 5.2f) * 9.0f;
        pose.gaze_y = std::sin(t * 2.6f) * 3.0f;
        pose.brow_tilt = 5.0f + soft * 2.0f;
        pose.brow_y = -2.0f;
        pose.mouth_curve = 2.0f + pulse * 5.0f;
        pose.mouth_width = 58.0f + pulse * 12.0f;
        pose.mouth_y = 199.0f + soft * 2.0f;
        pose.cheek = 0.14f + pulse * 0.12f;
        break;
    case Pet::Waiting:
        pose.eye_open = 1.02f + pulse * 0.18f;
        pose.eye_width = 88.0f + pulse * 8.0f;
        pose.gaze_y = -3.0f - pulse * 4.0f;
        pose.brow_tilt = -9.0f - pulse * 4.0f;
        pose.brow_y = -5.0f - pulse * 2.0f;
        pose.mouth_curve = -4.0f - pulse * 5.0f;
        pose.mouth_width = 44.0f + pulse * 10.0f;
        pose.cheek = 0.08f + pulse * 0.08f;
        break;
    case Pet::Done:
        pose.eye_open = 0.12f + pulse * 0.08f;
        pose.eye_width = 92.0f + pulse * 8.0f;
        pose.brow_y = -3.0f - pulse * 2.0f;
        pose.mouth_curve = 18.0f + pulse * 8.0f;
        pose.mouth_width = 90.0f + pulse * 14.0f;
        pose.mouth_y = 192.0f - pulse * 5.0f;
        pose.cheek = 0.68f + pulse * 0.32f;
        break;
    case Pet::Error:
        pose.eye_open = 0.72f + pulse * 0.24f;
        pose.gaze_x = std::sin(t * 12.0f) * 7.0f;
        pose.gaze_y = 6.0f + std::sin(t * 6.0f) * 2.0f;
        pose.brow_tilt = -12.0f - pulse * 7.0f;
        pose.mouth_curve = -13.0f - pulse * 9.0f;
        pose.mouth_width = 64.0f + pulse * 12.0f;
        pose.mouth_y = 205.0f + pulse * 4.0f;
        pose.cheek = 0.04f + pulse * 0.06f;
        break;
    case Pet::Paused:
        pose.eye_open = 0.20f + pulse * 0.08f;
        pose.gaze_x = 0.0f;
        pose.gaze_y = 7.0f + soft * 2.0f;
        pose.brow_y = 3.0f + pulse * 2.0f;
        pose.mouth_curve = -1.0f + soft * 2.0f;
        pose.mouth_width = 44.0f + pulse * 6.0f;
        pose.mouth_y = 201.0f + soft * 2.0f;
        pose.cheek = 0.04f + pulse * 0.04f;
        break;
    case Pet::Offline:
        pose.eye_open = 0.04f + pulse * 0.05f;
        pose.gaze_x = std::sin(t * 0.35f) * 2.0f;
        pose.gaze_y = 4.0f;
        pose.brow_y = 5.0f + soft;
        pose.mouth_curve = -2.0f + soft;
        pose.mouth_width = 40.0f + pulse * 5.0f;
        pose.cheek = 0.0f;
        break;
    case Pet::Searching:
        pose.eye_open = 0.82f + pulse * 0.16f;
        pose.gaze_x = std::sin(t * 2.7f) * 18.0f;
        pose.gaze_y = std::cos(t * 1.35f) * 5.0f;
        pose.brow_tilt = 3.0f + soft * 3.0f;
        pose.mouth_curve = 5.0f + pulse * 5.0f;
        pose.mouth_width = 62.0f + pulse * 12.0f;
        pose.cheek = 0.14f + pulse * 0.12f;
        break;
    default:
        break;
    }

    const float blink_phase = std::fmod(t, 4.85f);
    float blink = 1.0f;
    if (blink_phase < 0.18f) {
        blink = 1.0f - 0.94f * std::sin(blink_phase / 0.18f * Pi);
    } else if (blink_phase > 0.28f && blink_phase < 0.39f) {
        blink = 1.0f - 0.55f * std::sin((blink_phase - 0.28f) / 0.11f * Pi);
    }
    if (state != Pet::Offline && state != Pet::Done) {
        pose.eye_open *= blink;
    }
    return pose;
}

void approach(float &value, float target, float amount)
{
    value += (target - value) * amount;
}

void blend_pose(FacePose &pose, const FacePose &target, float amount)
{
    approach(pose.eye_open, target.eye_open, amount);
    approach(pose.eye_width, target.eye_width, amount);
    approach(pose.eye_height, target.eye_height, amount);
    approach(pose.gaze_x, target.gaze_x, amount);
    approach(pose.gaze_y, target.gaze_y, amount);
    approach(pose.brow_tilt, target.brow_tilt, amount);
    approach(pose.brow_y, target.brow_y, amount);
    approach(pose.mouth_curve, target.mouth_curve, amount);
    approach(pose.mouth_width, target.mouth_width, amount);
    approach(pose.mouth_y, target.mouth_y, amount);
    approach(pose.cheek, target.cheek, amount);
}

void update_connection(bool is_connected, bool usage_stale)
{
    (void)usage_stale;
    shown_connected = is_connected;
    lv_obj_set_style_bg_color(ui.connection_dot,
                              color(is_connected ? 0x43D895 : 0x66778C), 0);
    lv_label_set_text(ui.connection_text, is_connected ? "已连接" : "未连接");
}

uint32_t account_hash(const Pet::View &view)
{
    uint32_t hash = view.api_source | (view.api_mode << 4) | (view.account_kind << 8);
    hash = hash * 31 + view.week_remaining_x10;
    for (char c : view.plan_name) hash = hash * 31 + static_cast<uint8_t>(c);
    for (char c : view.week_reset) hash = hash * 31 + static_cast<uint8_t>(c);
    for (char c : view.membership_expires_at) hash = hash * 31 + static_cast<uint8_t>(c);
    return hash;
}

void quota_value(void *obj, int32_t value)
{
    lv_arc_set_value(static_cast<lv_obj_t *>(obj), value);
}

void update_account_ui(const Pet::View &view, Pet::Theme theme)
{
    const bool official = view.api_source == 1;
    const bool paper = theme == Pet::Paper;
    const bool custom = Character::supports(theme);
    const bool vangogh = theme == Pet::VanGogh;
    const auto &palette = theme_palette(theme);
    lv_obj_update_flag(ui.metric_rule, LV_OBJ_FLAG_HIDDEN, official && custom && !paper);
    if (view.api_source != shown_api_source) {
        lv_chart_set_all_value(ui.detail_chart, ui.input_series, 0);
        lv_chart_set_all_value(ui.detail_chart, ui.output_series, 0);
        shown_api_source = view.api_source;
    }
    for (lv_obj_t *obj : {ui.quota_ring, ui.quota_percent, ui.account_plan,
                          ui.account_expiry, ui.account_reset})
        lv_obj_update_flag(obj, LV_OBJ_FLAG_HIDDEN, !official);
    for (lv_obj_t *obj : {ui.balance_value, ui.models_caption})
        lv_obj_update_flag(obj, LV_OBJ_FLAG_HIDDEN, official);
    for (unsigned i = 0; i < Pet::ModelCount; ++i) {
        lv_obj_update_flag(ui.model_name[i], LV_OBJ_FLAG_HIDDEN, official || i >= 2);
        lv_obj_update_flag(ui.model_cost[i], LV_OBJ_FLAG_HIDDEN, official || i >= 2);
        lv_obj_update_flag(ui.detail_model_cost[i], LV_OBJ_FLAG_HIDDEN, official);
        lv_obj_update_flag(ui.detail_model_track[i], LV_OBJ_FLAG_HIDDEN, official);
        lv_obj_update_flag(ui.detail_model_fill[i], LV_OBJ_FLAG_HIDDEN, official);
    }
    lv_obj_set_style_text_font(ui.api_caption, official ? &font_menu_18 :
                              theme == Pet::Pixel ? &font_pixel_16 : &font_cn_18, 0);
    lv_label_set_text(ui.api_caption, official ? "OpenAI 周剩余额度" : "MoreCode · 余额");
    lv_label_set_text(ui.detail_subtitle, official ? "OpenAI" : "MoreCode API");
    lv_label_set_text(ui.detail_title, official ? "账户信息" : "今日统计");
    lv_obj_set_style_text_font(ui.detail_title, official ? &font_menu_18 : &font_cn_30, 0);
    lv_obj_set_y(ui.detail_title, official ? 22 : 13);
    lv_label_set_text(ui.detail_model_title, official ? "会员信息" : "各模型消费");
    lv_obj_set_style_text_font(ui.detail_model_title, official ? &font_menu_18 : &font_cn_18, 0);
    lv_label_set_text(ui.detail_chart_title, "当前对话 Token/s");
    lv_obj_set_style_text_font(ui.detail_chart_title, &font_menu_18, 0);
    const char *official_titles[] = {"周剩余额度", "会员等级", "Input Token", "Output Token", "周重置"};
    const char *morecode_titles[] = {"今日消费", "今日请求", "Input Token", "Output Token", "历史消费"};
    for (int i = 0; i < 5; ++i) {
        lv_label_set_text(ui.detail_titles[i], official ? official_titles[i] : morecode_titles[i]);
        lv_obj_set_style_text_font(ui.detail_titles[i], official ? &font_menu_18 : &font_cn_18, 0);
    }
    if (!official) {
        lv_obj_set_style_text_font(ui.detail_values[4], theme == Pet::Pixel ? &font_pixel_16 : &lv_font_montserrat_22, 0);
        for (unsigned i = 0; i < Pet::ModelCount; ++i) {
            const lv_font_t *font = theme == Pet::Pixel ? &font_pixel_16 : &font_cn_18;
            lv_obj_set_style_text_font(ui.detail_model_name[i], font, 0);
            lv_obj_set_style_text_font(ui.detail_model_tokens[i], font, 0);
        }
        return;
    }

    const int x = paper ? 28 : theme == Pet::Mechanical ? 28 : custom ? 572 : 580;
    const int y = paper ? 420 : custom ? 131 : vangogh ? 207 : 236;
    const int diameter = paper ? 54 : custom ? 116 : 100;
    lv_obj_set_pos(ui.quota_ring, x, y);
    lv_obj_set_size(ui.quota_ring, diameter, diameter);
    lv_obj_set_style_arc_color(ui.quota_ring, color(palette.track), LV_PART_MAIN);
    lv_obj_set_style_arc_color(ui.quota_ring,
                              color(view.week_remaining_x10 <= 150 ? 0xEF755F : palette.input),
                              LV_PART_INDICATOR);
    lv_obj_set_style_arc_width(ui.quota_ring, paper ? 4 : 7, LV_PART_MAIN);
    lv_obj_set_style_arc_width(ui.quota_ring, paper ? 4 : 7, LV_PART_INDICATOR);
    const int target = view.week_remaining_x10 == UINT16_MAX ? 0 : view.week_remaining_x10;
    if (account_hash(view) != shown_account_hash) {
        lv_anim_delete(ui.quota_ring, quota_value);
        lv_anim_t anim;
        lv_anim_init(&anim);
        lv_anim_set_var(&anim, ui.quota_ring);
        lv_anim_set_exec_cb(&anim, quota_value);
        lv_anim_set_values(&anim, lv_arc_get_value(ui.quota_ring), target);
        lv_anim_set_duration(&anim, 450);
        lv_anim_set_path_cb(&anim, lv_anim_path_ease_in_out);
        lv_anim_start(&anim);
    }
    char percent[20];
    if (view.week_remaining_x10 == UINT16_MAX)
        std::snprintf(percent, sizeof(percent), "--");
    else
        std::snprintf(percent, sizeof(percent), "%.0f%%", view.week_remaining_x10 / 10.0);
    lv_label_set_text(ui.quota_percent, percent);
    lv_obj_set_pos(ui.quota_percent, x, y + diameter / 2 - (paper ? 10 : 17));
    lv_obj_set_width(ui.quota_percent, diameter);
    lv_obj_set_style_text_align(ui.quota_percent, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_font(ui.quota_percent, paper ? &lv_font_montserrat_18 :
                              theme == Pet::Pixel ? &font_pixel_24 : &lv_font_montserrat_28, 0);
    const char *plan = view.plan_name[0] ? view.plan_name : view.account_kind == 2 ? "API Key" : "--";
    lv_label_set_text(ui.account_plan, plan);
    lv_obj_set_pos(ui.account_plan, x + diameter + 12, y + (paper ? 15 : 25));
    lv_obj_set_width(ui.account_plan, paper ? 146 : 196 - diameter - 12);
    lv_obj_set_style_text_font(ui.account_plan, &font_menu_18, 0);
    lv_label_set_long_mode(ui.account_plan, LV_LABEL_LONG_WRAP);
    if (view.account_kind != 2 && view.membership_expires_at[0])
        lv_label_set_text_fmt(ui.account_expiry, "到期: %.10s", view.membership_expires_at);
    else
        lv_label_set_text(ui.account_expiry, view.account_kind == 2 ? "API Key 无会员额度" : "到期: 未设置");
    lv_obj_set_pos(ui.account_expiry, paper ? 264 : x, paper ? 418 : custom ? 283 : vangogh ? 318 : 342);
    lv_obj_set_width(ui.account_expiry, paper ? 276 : 210);
    lv_obj_set_style_text_font(ui.account_expiry, &font_menu_18, 0);
    lv_label_set_text_fmt(ui.account_reset, "重置: %s", view.week_reset[0] ? view.week_reset : "--");
    lv_obj_set_pos(ui.account_reset, paper ? 264 : x, paper ? 449 : custom ? 317 : vangogh ? 343 : 369);
    lv_obj_set_width(ui.account_reset, paper ? 276 : 210);
    lv_obj_set_style_text_font(ui.account_reset, &font_menu_18, 0);
    lv_label_set_text(ui.detail_values[0], percent);
    lv_label_set_text(ui.detail_values[1], plan);
    lv_label_set_text(ui.detail_values[4], view.week_reset[0] ? view.week_reset : "--");
    lv_obj_set_style_text_font(ui.detail_values[4], &lv_font_montserrat_16, 0);
    char expiry[17];
    std::memcpy(expiry, view.membership_expires_at, sizeof(expiry));
    if (expiry[0]) expiry[10] = ' ';
    const char *names[] = {"会员等级", "会员到期(手动)", "周额度重置", "API 来源"};
    const char *values[] = {plan, view.account_kind == 2 ? "--" : expiry[0] ? expiry : "未设置",
                           view.week_reset[0] ? view.week_reset : "--", "OpenAI"};
    for (unsigned i = 0; i < Pet::ModelCount; ++i) {
        lv_label_set_text(ui.detail_model_name[i], names[i]);
        lv_label_set_text(ui.detail_model_tokens[i], values[i]);
        lv_obj_set_style_text_font(ui.detail_model_name[i], &font_menu_18, 0);
        lv_obj_set_style_text_font(ui.detail_model_tokens[i], &font_menu_18, 0);
    }
}

void update_static_state(const Pet::View &view, bool is_connected)
{
    const Pet::State state = view.state < Pet::StateCount ? view.state : Pet::Offline;
    const Pet::Theme theme = active_theme(view.theme);
    const bool theme_changed = theme != shown_theme;
    if (theme_changed) {
        apply_theme(theme);
        title_transition_active = false;
    }
    const bool vangogh = theme == Pet::VanGogh;
    lv_label_set_text(ui.state_name, vangogh
                          ? (state == Pet::Done ? "已完成" : "思考中")
                          : StateNames[state]);
    lv_label_set_text(ui.state_detail, vangogh
                          ? (state == Pet::Done ? "星空缓缓流动" : "星辰随月光流转")
                          : StateDetails[state]);
#ifndef PET_DESKTOP_PREVIEW
    if (CelestialDevice::adaptive() && !CelestialDevice::active()) {
        lv_label_set_text(ui.state_detail, "等待本地 SD 素材");
    }
#endif
    char duration[32];
    format_duration(view.duration_seconds, duration, sizeof(duration));
    lv_label_set_text(ui.session_duration,
                      Character::supports(theme) ? duration + 5 : duration);
    lv_label_set_text(ui.effect, StateGlyphs[state]);

    char metric[48];
    format_money(view.balance_quota, view.quota_per_unit, metric, sizeof(metric));
    lv_label_set_text(ui.balance_value, metric);

    for (unsigned i = 0; i < Pet::ModelCount; ++i) {
        const bool available = i < view.model_count && view.models[i].name[0];
        lv_label_set_text(ui.model_name[i], available ? view.models[i].name : "-");
        format_money(available ? view.models[i].quota : UINT32_MAX,
                     view.quota_per_unit, metric, sizeof(metric));
        lv_label_set_text(ui.model_cost[i], metric);
        lv_obj_set_style_opa(ui.model_name[i], available ? LV_OPA_COVER : LV_OPA_40, 0);
        lv_obj_set_style_opa(ui.model_cost[i], available ? LV_OPA_COVER : LV_OPA_40, 0);

        lv_label_set_text(ui.detail_model_name[i],
                          available ? view.models[i].name : "暂无模型数据");
        lv_label_set_text(ui.detail_model_cost[i], metric);
        char token_text[48];
        if (available) {
            std::snprintf(token_text, sizeof(token_text), "%lu 请求 · ",
                          static_cast<unsigned long>(view.models[i].request_count));
            const size_t used = std::strlen(token_text);
            format_tokens(view.models[i].prompt_tokens +
                              view.models[i].completion_tokens,
                          token_text + used, sizeof(token_text) - used);
            std::strncat(token_text, " Token",
                         sizeof(token_text) - std::strlen(token_text) - 1);
        } else {
            std::snprintf(token_text, sizeof(token_text), "--");
        }
        lv_label_set_text(ui.detail_model_tokens[i], token_text);
        const uint32_t top_quota =
            view.model_count && view.models[0].quota ? view.models[0].quota : 1;
        const int width = available
                              ? static_cast<int>(std::clamp<uint64_t>(
                                    230ULL * view.models[i].quota / top_quota, 4, 230))
                              : 0;
        lv_obj_set_width(ui.detail_model_fill[i], width);
    }

    format_rate(view.input_tps_x10, metric, sizeof(metric));
    lv_label_set_text(ui.input_rate, metric);
    lv_label_set_text(ui.detail_input_rate, metric);
    format_rate(view.output_tps_x10, metric, sizeof(metric));
    lv_label_set_text(ui.output_rate, metric);
    lv_label_set_text(ui.detail_output_rate, metric);

    format_money(view.today_quota, view.quota_per_unit, metric, sizeof(metric));
    lv_label_set_text(ui.detail_values[0], metric);
    if (view.today_request_count == UINT32_MAX) {
        std::snprintf(metric, sizeof(metric), "--");
    } else {
        std::snprintf(metric, sizeof(metric), "%lu",
                      static_cast<unsigned long>(view.today_request_count));
    }
    lv_label_set_text(ui.detail_values[1], metric);
    format_tokens(view.today_prompt_tokens, metric, sizeof(metric));
    lv_label_set_text(ui.detail_values[2], metric);
    format_tokens(view.today_completion_tokens, metric, sizeof(metric));
    lv_label_set_text(ui.detail_values[3], metric);
    format_money(view.spent_quota, view.quota_per_unit, metric, sizeof(metric));
    lv_label_set_text(ui.detail_values[4], metric);
    lv_label_set_text(ui.detail_freshness,
                      !is_connected ? "桌面端未连接" :
                      view.usage_stale ? "缓存数据" : "实时数据");
    update_account_ui(view, theme);
    update_connection(is_connected, view.usage_stale != 0);

    const uint32_t new_labels_hash = labels_hash(view);
    const bool labels_changed = new_labels_hash != shown_labels_hash;
    if (labels_changed && shown_labels_hash != UINT32_MAX && !theme_changed) {
        if (title_transition_active) {
            finish_title_transition(theme);
        }
        begin_title_transition(view, theme);
    } else if (!title_transition_active) {
        paint_session_titles(view, theme);
    }
    shown_state = state;
    shown_balance_quota = view.balance_quota;
    shown_spent_quota = view.spent_quota;
    shown_request_count = view.request_count;
    shown_today_request_count = view.today_request_count;
    shown_today_quota = view.today_quota;
    shown_today_prompt_tokens = view.today_prompt_tokens;
    shown_today_completion_tokens = view.today_completion_tokens;
    shown_input_tps_x10 = view.input_tps_x10;
    shown_output_tps_x10 = view.output_tps_x10;
    shown_quota_per_unit = view.quota_per_unit;
    shown_usage_stale = view.usage_stale;
    shown_account_hash = account_hash(view);
    shown_duration_seconds = view.duration_seconds;
    shown_models_hash = model_hash(view);
    shown_labels_hash = new_labels_hash;
}

void request_session_step(int delta)
{
    portENTER_CRITICAL(&view_mux);
    session_step_pending += delta;
    portEXIT_CRITICAL(&view_mux);
}

int consume_session_step()
{
    int delta;
    portENTER_CRITICAL(&view_mux);
    delta = session_step_pending;
    session_step_pending = 0;
    portEXIT_CRITICAL(&view_mux);
    return delta;
}

bool hit_object(lv_obj_t *object, const lv_point_t &point)
{
    lv_area_t area;
    lv_obj_get_coords(object, &area);
    return point.x >= area.x1 && point.x <= area.x2 &&
           point.y >= area.y1 && point.y <= area.y2;
}

bool hit_home_data(const lv_point_t &point)
{
    return hit_object(ui.data_panel, point) &&
           (Character::supports(shown_theme) || point.y >= 194);
}

void gesture_event(lv_event_t *event)
{
    if (ModelPicker::visible()) return;
    const lv_event_code_t code = lv_event_get_code(event);
    lv_indev_t *indev = lv_indev_active();
    if (!indev) {
        return;
    }
    lv_point_t point;
    lv_indev_get_point(indev, &point);
    if (code == LV_EVENT_PRESSED) {
        press_point = point;
        press_brightness = brightness;
        long_press_handled = false;
        touch_energy = 1.0f;
    } else if (code == LV_EVENT_PRESSING) {
        const int dy = point.y - press_point.y;
        if (!detail_target && detail_progress < 0.05f &&
            hit_object(ui.stage, press_point) && std::abs(dy) > 12) {
            set_brightness(press_brightness - dy * 100 / 260);
        }
    } else if (code == LV_EVENT_LONG_PRESSED) {
        if (!detail_target && detail_progress < 0.05f) {
            long_press_handled = true;
        }
    } else if (code == LV_EVENT_RELEASED) {
        touch_energy = 0.0f;
        if (!long_press_handled) {
            const int dx = point.x - press_point.x;
            const int dy = point.y - press_point.y;
            if (detail_target || detail_progress > 0.5f) {
                if ((dx > 70 && std::abs(dx) > std::abs(dy)) ||
                    (std::abs(dx) < 18 && std::abs(dy) < 18 &&
                     point.x < 92 && point.y < 72)) {
                    detail_target = false;
                }
            } else if (std::abs(dx) < 18 && std::abs(dy) < 18 &&
                       hit_home_data(press_point) && hit_home_data(point)) {
                detail_target = true;
                lv_obj_clear_flag(ui.detail_page, LV_OBJ_FLAG_HIDDEN);
#ifdef PET_DESKTOP_PREVIEW
                lv_obj_move_foreground(ui.gesture_layer);
#endif
            } else if (std::abs(dx) > 70 && std::abs(dx) > std::abs(dy)) {
                request_session_step(dx < 0 ? 1 : -1);
            }
        }
    } else if (code == LV_EVENT_PRESS_LOST) {
        touch_energy = 0.0f;
    }
}

void update_eye(lv_obj_t *eye, lv_obj_t *glow, lv_obj_t *pupil, lv_obj_t *glint,
                int center_x, int center_y, const FacePose &pose, Pet::Theme theme,
                uint32_t accent)
{
    const bool beach = theme == Pet::Beach;
    const bool pixel = theme == Pet::Pixel;
    const bool vangogh = theme == Pet::VanGogh;
    const int width = static_cast<int>(pose.eye_width);
    const int animated_height = std::max(
        0, static_cast<int>(std::lround(pose.eye_height * pose.eye_open)));
    const bool closed = animated_height <= (vangogh ? 13 : 6);
    const int height = closed ? (pixel ? 6 : vangogh ? 3 : 4) : animated_height;
    const int x = center_x - width / 2;
    const int y = center_y - height / 2;
    const int glow_pad = vangogh ? 5 : 7;
    lv_obj_set_pos(glow, x - glow_pad, y - glow_pad);
    lv_obj_set_size(glow, width + glow_pad * 2, height + glow_pad * 2);
    lv_obj_set_pos(eye, x, y);
    lv_obj_set_size(eye, width, height);

    const int pupil_w = std::max(8, static_cast<int>((beach ? 31.0f :
                                                       pixel ? 30.0f :
                                                       vangogh ? 20.0f : 27.0f) *
                                                       std::min(pose.eye_open, 1.0f)));
    const int pupil_h = std::max(5, static_cast<int>((beach ? 30.0f :
                                                       pixel ? 34.0f :
                                                       vangogh ? 31.0f : 42.0f) *
                                                       std::min(pose.eye_open, 1.0f)));
    const int max_x = std::max(0, (width - pupil_w) / 2 - 5);
    const int max_y = std::max(0, (height - pupil_h) / 2 - 3);
    const int pupil_x = width / 2 - pupil_w / 2 +
                        std::clamp(static_cast<int>(pose.gaze_x), -max_x, max_x);
    const int pupil_y = height / 2 - pupil_h / 2 +
                        std::clamp(static_cast<int>(pose.gaze_y), -max_y, max_y);
    lv_obj_set_pos(pupil, pupil_x, pupil_y);
    lv_obj_set_size(pupil, pupil_w, pupil_h);
    lv_obj_set_style_bg_opa(
        pupil, closed || height < 12 ? LV_OPA_TRANSP : LV_OPA_COVER, 0);
    lv_obj_set_pos(glint, pupil_w / 5, pupil_h / 6);
    lv_obj_set_size(glint, vangogh ? 4 : 7, vangogh ? 6 : 10);
    lv_obj_set_style_bg_opa(
        glint, closed || height < 22 ? LV_OPA_TRANSP : LV_OPA_80, 0);
    const ThemePalette &palette = theme_palette(theme);
    const uint32_t closed_color =
        beach ? 0xE4A928 :
        pixel ? 0xFF5FA2 :
        vangogh ? 0xF4C95D :
        mix_rgb(accent, 0xFFFFFF, 0.55f);
    lv_obj_set_style_bg_color(eye, color(closed ? closed_color : palette.eye), 0);
    lv_obj_set_style_bg_opa(
        eye, vangogh && !closed
                 ? static_cast<lv_opa_t>(190)
                 : static_cast<lv_opa_t>(LV_OPA_COVER),
        0);
    if (vangogh) {
        lv_obj_set_style_border_opa(
            eye, closed
                     ? static_cast<lv_opa_t>(LV_OPA_TRANSP)
                     : static_cast<lv_opa_t>(158),
            0);
    }
    lv_obj_set_style_radius(
        eye, closed && pixel ? 0 : LV_RADIUS_CIRCLE, 0);
}

void update_lines(const FacePose &pose, uint32_t accent)
{
    const int tilt = static_cast<int>(pose.brow_tilt);
    brow_left_points[0] = {0, static_cast<lv_value_precise_t>(5 - tilt)};
    brow_left_points[1] = {27, 0};
    brow_left_points[2] = {54, static_cast<lv_value_precise_t>(5 + tilt)};
    brow_right_points[0] = {0, static_cast<lv_value_precise_t>(5 + tilt)};
    brow_right_points[1] = {27, 0};
    brow_right_points[2] = {54, static_cast<lv_value_precise_t>(5 - tilt)};
    lv_line_set_points(ui.brow_left, brow_left_points, 3);
    lv_line_set_points(ui.brow_right, brow_right_points, 3);
    const bool vangogh = shown_theme == Pet::VanGogh;
    lv_obj_set_pos(ui.brow_left, vangogh ? 110 : 77,
                   (vangogh ? 61 : 48) + static_cast<int>(pose.brow_y));
    lv_obj_set_pos(ui.brow_right, vangogh ? 248 : 271,
                   (vangogh ? 61 : 48) + static_cast<int>(pose.brow_y));

    const int width = static_cast<int>(pose.mouth_width);
    const int curve = static_cast<int>(pose.mouth_curve);
    mouth_points[0] = {0, 0};
    mouth_points[1] = {static_cast<lv_value_precise_t>(width / 4),
                       static_cast<lv_value_precise_t>(curve * 3 / 4)};
    mouth_points[2] = {static_cast<lv_value_precise_t>(width / 2),
                       static_cast<lv_value_precise_t>(curve)};
    mouth_points[3] = {static_cast<lv_value_precise_t>(width * 3 / 4),
                       static_cast<lv_value_precise_t>(curve * 3 / 4)};
    mouth_points[4] = {static_cast<lv_value_precise_t>(width), 0};
    lv_line_set_points(ui.mouth, mouth_points, 5);
    lv_obj_set_pos(ui.mouth, (vangogh ? 205 : 202) - width / 2,
                   static_cast<int>(pose.mouth_y));

    const lv_color_t line_color =
        color(shown_theme == Pet::Beach ? 0xE4A928 :
              shown_theme == Pet::Pixel ? 0xFF5FA2 :
              shown_theme == Pet::VanGogh ? 0xF4C95D :
              mix_rgb(accent, 0xFFFFFF, 0.55f));
    lv_obj_set_style_line_color(ui.brow_left, line_color, 0);
    lv_obj_set_style_line_color(ui.brow_right, line_color, 0);
    lv_obj_set_style_line_color(ui.mouth, line_color, 0);
}

void update_pixel_features(const FacePose &pose, Pet::State state, float t,
                           uint32_t accent)
{
    constexpr uint32_t colors[6] = {
        0x43F5B5, 0x4DDCFF, 0x6C4BFF, 0xFF5FA2, 0xFFF36D, 0xFF8A4D,
    };
    const int frame = static_cast<int>(t * 8.0f);
    if (frame == pixel_feature_frame && state == pixel_feature_state) {
        return;
    }
    pixel_feature_frame = frame;
    pixel_feature_state = state;
    t = frame / 8.0f;
    const float pulse = (std::sin(t * 4.0f) + 1.0f) * 0.5f;
    const float scan_speed =
        state == Pet::Working ? 1.8f :
        state == Pet::Searching ? 1.4f :
        state == Pet::Error ? 3.4f :
        state == Pet::Offline ? 0.28f : 0.72f;
    const int scan_y =
        24 + static_cast<int>(std::fmod(t * scan_speed, 1.0f) * 314.0f);
    lv_obj_set_y(ui.pixel_scanline, scan_y);
    lv_obj_set_style_bg_color(
        ui.pixel_scanline,
        color(state == Pet::Error ? 0xFF5FA2 :
              state == Pet::Done ? 0xFFF36D : 0x4DDCFF), 0);
    lv_obj_set_style_opa(
        ui.pixel_scanline,
        static_cast<lv_opa_t>(state == Pet::Offline ? 42 : 72 + pulse * 64), 0);

    for (int i = 0; i < 8; ++i) {
        const int offset = state == Pet::Searching ? (frame + i) % 4 : 0;
        lv_obj_set_x(ui.pixel_grid_v[i], 56 + i * 58 + offset);
        lv_obj_set_style_opa(
            ui.pixel_grid_v[i],
            static_cast<lv_opa_t>(state == Pet::Offline ? 24 : 35 + pulse * 24), 0);
    }
    for (int i = 0; i < 5; ++i) {
        const int offset = state == Pet::Error ? ((frame + i) & 1) * 3 : 0;
        lv_obj_set_y(ui.pixel_grid_h[i], 62 + i * 58 + offset);
        lv_obj_set_style_opa(
            ui.pixel_grid_h[i],
            static_cast<lv_opa_t>(state == Pet::Offline ? 20 : 38 + pulse * 22), 0);
    }

    int brow_left_y[3] = {2, 0, 2};
    int brow_right_y[3] = {2, 0, 2};
    int mouth_y[5] = {0, 4, 8, 4, 0};
    switch (state) {
    case Pet::Thinking:
        brow_left_y[0] = -2;
        brow_left_y[1] = -6 - ((frame / 3) & 1) * 4;
        brow_left_y[2] = -2;
        brow_right_y[0] = 5;
        brow_right_y[1] = 2;
        brow_right_y[2] = 0;
        mouth_y[0] = mouth_y[4] = 4;
        mouth_y[1] = mouth_y[2] = mouth_y[3] = 2;
        break;
    case Pet::Working:
        brow_left_y[0] = 0;
        brow_left_y[1] = 3;
        brow_left_y[2] = 6;
        brow_right_y[0] = 6;
        brow_right_y[1] = 3;
        brow_right_y[2] = 0;
        for (int i = 0; i < 5; ++i) {
            mouth_y[i] = ((frame + i) & 1) ? 7 : 0;
        }
        break;
    case Pet::Waiting:
        for (int i = 0; i < 3; ++i) {
            brow_left_y[i] = brow_right_y[i] = -7 - static_cast<int>(pulse * 4);
        }
        mouth_y[0] = mouth_y[4] = 8;
        mouth_y[1] = mouth_y[3] = 3;
        mouth_y[2] = 0;
        break;
    case Pet::Done:
        brow_left_y[0] = brow_left_y[2] = 5;
        brow_left_y[1] = -2;
        brow_right_y[0] = brow_right_y[2] = 5;
        brow_right_y[1] = -2;
        mouth_y[0] = mouth_y[4] = 0;
        mouth_y[1] = mouth_y[3] = 7;
        mouth_y[2] = 12;
        break;
    case Pet::Error:
        brow_left_y[0] = 0;
        brow_left_y[1] = 5;
        brow_left_y[2] = 10;
        brow_right_y[0] = 10;
        brow_right_y[1] = 5;
        brow_right_y[2] = 0;
        for (int i = 0; i < 5; ++i) {
            mouth_y[i] = ((frame + i) & 1) ? 1 : 10;
        }
        break;
    case Pet::Paused:
        for (int i = 0; i < 3; ++i) {
            brow_left_y[i] = brow_right_y[i] = 7;
            mouth_y[i + 1] = 3;
        }
        mouth_y[0] = mouth_y[4] = 3;
        break;
    case Pet::Offline:
        for (int i = 0; i < 3; ++i) {
            brow_left_y[i] = brow_right_y[i] = 10;
        }
        for (int i = 0; i < 5; ++i) {
            mouth_y[i] = 4;
        }
        break;
    case Pet::Searching:
        for (int i = 0; i < 3; ++i) {
            brow_left_y[i] = ((frame + i) % 3) * 3;
            brow_right_y[i] = ((frame + 2 - i) % 3) * 3;
        }
        mouth_y[0] = mouth_y[4] = 2;
        mouth_y[1] = mouth_y[3] = 5;
        mouth_y[2] = 7;
        break;
    default:
        break;
    }

    const int brow_base_y = 49 + static_cast<int>(pose.brow_y);
    for (int i = 0; i < 3; ++i) {
        lv_obj_set_pos(ui.pixel_brow_left[i], 78 + i * 19,
                       brow_base_y + brow_left_y[i]);
        lv_obj_set_pos(ui.pixel_brow_right[i], 270 + i * 19,
                       brow_base_y + brow_right_y[i]);
        const uint32_t brow_color =
            state == Pet::Error ? 0xFF5FA2 :
            state == Pet::Waiting ? 0xFFF36D : accent;
        lv_obj_set_style_bg_color(ui.pixel_brow_left[i], color(brow_color), 0);
        lv_obj_set_style_bg_color(ui.pixel_brow_right[i], color(brow_color), 0);
    }

    const int mouth_base_x = 158;
    const int mouth_base_y = static_cast<int>(pose.mouth_y) - 7;
    for (int i = 0; i < 5; ++i) {
        const bool glitch_hidden =
            state == Pet::Error && ((frame + i * 3) % 11 == 0);
        lv_obj_set_pos(ui.pixel_mouth[i], mouth_base_x + i * 20,
                       mouth_base_y + mouth_y[i]);
        lv_obj_set_style_bg_color(
            ui.pixel_mouth[i],
            color(state == Pet::Done ? colors[(frame + i) % 6] :
                  state == Pet::Error ? 0xFF5FA2 : 0xFFF36D), 0);
        lv_obj_set_style_opa(ui.pixel_mouth[i],
                             glitch_hidden ? LV_OPA_TRANSP : LV_OPA_COVER, 0);
    }

    int active_segments =
        state == Pet::Working ? 2 + (frame % 7) :
        state == Pet::Searching ? 1 + (frame % 8) :
        state == Pet::Done ? 8 :
        state == Pet::Thinking ? 3 + ((frame / 2) % 4) :
        state == Pet::Waiting ? (frame & 1 ? 8 : 2) :
        state == Pet::Error ? ((frame & 1) ? 8 : 0) :
        state == Pet::Paused ? 2 :
        state == Pet::Offline ? 1 : 3 + static_cast<int>(pulse * 2);
    for (int i = 0; i < 8; ++i) {
        lv_obj_set_style_bg_color(ui.pixel_meter[i], color(colors[i % 6]), 0);
        lv_obj_set_style_opa(
            ui.pixel_meter[i],
            i < active_segments ? static_cast<lv_opa_t>(LV_OPA_COVER) :
                                  static_cast<lv_opa_t>(
                                      state == Pet::Offline ? 18 : 40), 0);
    }

    const int ear_step = ((frame / 2) & 1) * 4;
    lv_obj_set_y(ui.pixel_ear_left, 126 + ear_step);
    lv_obj_set_y(ui.pixel_ear_right, 126 + (state == Pet::Working ? 4 - ear_step : ear_step));
    lv_obj_set_style_bg_color(
        ui.pixel_ear_left, color(state == Pet::Error ? 0xFF5FA2 : 0x6C4BFF), 0);
    lv_obj_set_style_bg_color(
        ui.pixel_ear_right, color(state == Pet::Done ? 0xFFF36D : 0x6C4BFF), 0);

    for (int i = 0; i < 6; ++i) {
        const int height = 5 + ((frame + i + static_cast<int>(state)) % 3) * 2;
        lv_obj_set_height(ui.pixel_top_strip[i], height);
    }
    lv_obj_set_style_bg_color(ui.aura_outer,
                              color(mix_rgb(accent, 0x6C4BFF, 0.45f)), 0);
    lv_obj_set_style_bg_color(ui.aura_inner,
                              color(mix_rgb(accent, 0x09091D, 0.72f)), 0);
    lv_obj_set_style_bg_opa(ui.aura_outer,
                            static_cast<lv_opa_t>(48 + pulse * 28), 0);
    lv_obj_set_style_bg_opa(ui.aura_inner,
                            static_cast<lv_opa_t>(62 + pulse * 34), 0);
    lv_obj_set_style_border_color(ui.face, color(accent), 0);
    lv_obj_set_style_bg_color(ui.state_dot, color(accent), 0);
    lv_obj_set_style_text_color(ui.effect, color(accent), 0);
}

void update_vangogh_features(Pet::State state, float t, float dt)
{
    const float target = state == Pet::Done ? 0.0f : 1.0f;
    vangogh_moon_progress += (target - vangogh_moon_progress) *
                             (1.0f - std::exp(-dt * 2.2f));
    vangogh_state_energy += (target - vangogh_state_energy) *
                            (1.0f - std::exp(-dt * 1.8f));
    const int frame = static_cast<int>(t * 30.0f);
    if (frame == vangogh_animation_frame) return;
    vangogh_animation_frame = frame;
    t = frame / 30.0f;

    // The moon follows the rising painted ribbon in a cubic arc. The
    // continuously eased parameter also allows an interruption mid-flight.
    const float p = vangogh_moon_progress;
    const float q = 1.0f - p;
    const float moon_x = q*q*q*488 + 3*q*q*p*447 + 3*q*p*p*351 + p*p*p*275;
    const float moon_y = q*q*q*55 + 3*q*q*p*108 + 3*q*p*p*21 + p*p*p*99;
    const float breathe = std::sin(t * 0.91f) * 0.5f +
                          std::sin(t * 1.61f + 0.8f) * 0.18f +
                          std::sin(t * 2.7f + 1.4f) * 0.10f;
    lv_obj_set_pos(ui.vangogh_moon,
                   static_cast<int>(std::lround(moon_x)) - 62,
                   static_cast<int>(std::lround(moon_y)) - 55);
    lv_obj_set_style_opa(ui.vangogh_moon,
                         static_cast<lv_opa_t>(220 + breathe * 26), 0);

    constexpr int star_xy[6][2] = {
        {58, 7}, {127, 59}, {174, 125}, {193, 209}, {328, 22}, {379, 85},
    };
    for (int i = 0; i < 12; ++i) {
        const float phase = i * 1.89f;
        const float shimmer = 0.5f + 0.5f * std::sin(
            t * (0.67f + (i % 3) * 0.15f) + phase);
        if (i < 6) {
            lv_obj_set_pos(ui.vangogh_star[i], star_xy[i][0] - 10,
                           star_xy[i][1] - 10);
            lv_obj_set_style_opa(
                ui.vangogh_star[i],
                static_cast<lv_opa_t>(18 + 95 * shimmer), 0);
        } else {
            const float angle = t * (0.64f + (i % 3) * 0.08f) + phase;
            const float radius = 60.0f + (i % 3) * 12.0f;
            const float x = moon_x + std::cos(angle) * radius +
                            std::sin(angle * 2) * 4;
            const float y = moon_y + std::sin(angle) * radius * 0.60f +
                            std::cos(angle * 2) * 5;
            lv_obj_set_pos(ui.vangogh_star[i],
                           static_cast<int>(x) - 10, static_cast<int>(y) - 10);
            lv_obj_set_style_opa(ui.vangogh_star[i],
                                 static_cast<lv_opa_t>(
                                     p * (68 + shimmer * 110)), 0);
        }
    }

    constexpr uint32_t stroke_colors[8] = {
        0xACC4B3, 0x638DA9, 0xDDC66F, 0x4A829E,
        0x9BB3BA, 0xC4B36C, 0x9DB7AF, 0xD2C078,
    };
    for (int line = 0; line < 8; ++line) {
        for (int i = 0; i < 10; ++i) {
            const float fraction = i / 9.0f;
            float x, y;
            if (line < 4) {
                const float angle = t * (0.27f + p * 0.10f) +
                                    line * 1.61f + fraction * 0.75f;
                const float radius = 42.0f + line * 19.0f;
                x = 272 + std::cos(angle) * radius;
                y = 142 + std::sin(angle) * radius * 0.59f;
            } else if (line < 6) {
                const float angle = -t * 0.34f + line * 2.20f +
                                    fraction * 1.15f;
                const float radius = 21.0f + (line - 4) * 16.0f;
                x = 375 + std::cos(angle) * radius;
                y = 191 + std::sin(angle) * radius * 0.64f;
            } else {
                const float angle = t * 0.25f + line * Pi + fraction * 0.33f;
                x = 308 + std::cos(angle) * 133.0f;
                y = 244 + std::sin(angle) * 19.0f +
                    std::sin(x * 0.024f) * 9.0f;
            }
            vangogh_swirl_points[line][i] = {
                static_cast<lv_value_precise_t>(x),
                static_cast<lv_value_precise_t>(y),
            };
        }
        lv_line_set_points(ui.vangogh_swirl[line], vangogh_swirl_points[line], 10);
        lv_obj_set_style_line_color(ui.vangogh_swirl[line],
                                     color(stroke_colors[line]), 0);
        lv_obj_set_style_line_opa(
            ui.vangogh_swirl[line],
            static_cast<lv_opa_t>(37 + p * 24 +
                24 * (0.5f + 0.5f * std::sin(t * .58f + line))), 0);
    }

    const float flow_phase = t * (0.34f + vangogh_state_energy * 0.18f);
    const float flow_gain = 0.65f + vangogh_state_energy * 0.75f;
    constexpr uint32_t flow_colors[3] = {0x789FA9, 0xB3B47A, 0x5A8199};
    // Shared phase ribbons make the sky, hills and vortex move as one field.
    for (int line = 0; line < 2; ++line) {
        for (int i = 0; i < 6; ++i) {
            const float u = i / 5.0f;
            const float angle = -1.0f + u * (Pi * 1.55f) +
                                flow_phase * (0.18f + line * 0.012f) +
                                line * 0.14f;
            const float radius = 25.0f + line * 15.0f +
                std::sin(flow_phase * 1.2f + u * 4.0f + line * 0.51f) *
                3.0f * flow_gain;
            vangogh_flow_points[line][i] = {
                static_cast<lv_value_precise_t>(286.0f + std::cos(angle) * radius),
                static_cast<lv_value_precise_t>(130.0f +
                    std::sin(angle) * radius * 0.57f),
            };
        }
        lv_line_set_points(ui.vangogh_flow[line], vangogh_flow_points[line], 6);
        lv_obj_set_style_line_color(ui.vangogh_flow[line],
                                    color(flow_colors[line % 3]), 0);
        lv_obj_set_style_line_opa(ui.vangogh_flow[line],
            static_cast<lv_opa_t>(42 + vangogh_state_energy * 42 +
                25 * (0.5f + 0.5f * std::sin(flow_phase * 1.3f + line))), 0);
    }
    for (int line = 2; line < 4; ++line) {
        const int band = line - 2;
        for (int i = 0; i < 6; ++i) {
            const float u = i / 5.0f;
            const float x = 30.0f + u * 480.0f;
            const float contour = std::sin(u * Pi * (1.15f + band * .06f) +
                                           band * .7f) * (8.0f + band);
            const float wave = std::sin(flow_phase * .82f + u * 3.0f +
                                        band * .45f) * (2.0f + band * .5f) *
                               flow_gain;
            vangogh_flow_points[line][i] = {
                static_cast<lv_value_precise_t>(x),
                static_cast<lv_value_precise_t>(211.0f + band * 12.0f +
                                                contour + wave),
            };
        }
        lv_line_set_points(ui.vangogh_flow[line], vangogh_flow_points[line], 6);
        lv_obj_set_style_line_color(ui.vangogh_flow[line],
                                    color(line & 1 ? 0x6E8E8C : 0xA69C6B), 0);
        lv_obj_set_style_line_opa(ui.vangogh_flow[line],
            static_cast<lv_opa_t>(30 + vangogh_state_energy * 30 +
                18 * (0.5f + 0.5f * std::sin(flow_phase + band))), 0);
    }

    // Displaced impasto sits within the cypress silhouette. The upper tips
    // bend further than the trunk and each branch has its own wind phase.
    constexpr int branches[4][7][2] = {
        {{104, 14}, {102, 42}, {104, 75}, {111, 109}, {112, 139}, {111, 169}, {112, 195}},
        {{96, 88}, {91, 116}, {90, 147}, {92, 179}, {95, 209}, {99, 237}, {103, 267}},
        {{153, 172}, {155, 190}, {158, 210}, {165, 230}, {173, 249}, {178, 269}, {183, 289}},
        {{70, 181}, {69, 208}, {72, 231}, {74, 253}, {80, 283}, {88, 309}, {96, 337}},
    };
    constexpr uint32_t tree_colors[4] = {
        0x728075, 0xA29872, 0x6D826F, 0x665E48,
    };
    for (int line = 0; line < 4; ++line) {
        for (int i = 0; i < 7; ++i) {
            const int y = branches[line][i][1];
            const float sway = (1.0f - y / 390.0f) *
                (4.0f * std::sin(flow_phase * 1.1f + y * .024f + line * 1.13f) +
                 2.0f * std::sin(flow_phase * 1.8f - y * .018f + line)) *
                (0.55f + vangogh_state_energy * 0.7f);
            vangogh_tree_points[line][i] = {
                static_cast<lv_value_precise_t>(branches[line][i][0] + sway),
                static_cast<lv_value_precise_t>(y +
                    std::sin(flow_phase + i * .34f + line) *
                    (1.0f + vangogh_state_energy * 1.5f)),
            };
        }
        lv_line_set_points(ui.vangogh_tree[line], vangogh_tree_points[line], 7);
        lv_obj_set_style_line_color(ui.vangogh_tree[line],
                                    color(tree_colors[line]), 0);
        lv_obj_set_style_line_opa(
            ui.vangogh_tree[line],
            static_cast<lv_opa_t>(48 + vangogh_state_energy * 20 +
                24 * (0.5f + 0.5f * std::sin(flow_phase * .7f + line))), 0);
    }
    for (int i = 0; i < 6; ++i) {
        const float glow = (std::sin(t * 0.38f + i * 1.21f) + 1.0f) * 0.5f;
        lv_obj_set_style_bg_opa(ui.vangogh_window[i],
                                 static_cast<lv_opa_t>(55 + glow * 105), 0);
    }
}

void update_particles(Pet::State state, float t, uint32_t accent)
{
    if (shown_theme == Pet::Pixel) {
        const int frame = static_cast<int>(t * 12.0f);
        if (frame == pixel_particle_frame && state == pixel_particle_state) {
            return;
        }
        pixel_particle_frame = frame;
        pixel_particle_state = state;
        t = frame / 12.0f;
        constexpr uint32_t colors[] = {
            0x43F5B5, 0xFF5FA2, 0xFFF36D, 0x6C4BFF, 0x4DDCFF, 0xFF8A4D,
        };
        const float speed =
            state == Pet::Working ? 3.2f :
            state == Pet::Searching ? 2.6f :
            state == Pet::Error ? 5.0f : 1.5f;
        for (int i = 0; i < 6; ++i) {
            const float phase = std::fmod(t * (0.18f + speed * 0.025f) +
                                              i * 0.173f, 1.0f);
            int x = 72 + i * 72;
            int y = 292 - static_cast<int>(phase * 230.0f);
            if (state == Pet::Thinking || state == Pet::Searching) {
                const float angle = t * speed + i * Pi / 3.0f;
                x = 252 + static_cast<int>(190.0f * std::cos(angle));
                y = 160 + static_cast<int>(120.0f * std::sin(angle));
            } else if (state == Pet::Error) {
                x += static_cast<int>(std::sin(t * 16.0f + i) * 18.0f);
                y = 65 + i * 42;
            } else if (state == Pet::Paused || state == Pet::Offline) {
                x = 110 + i * 62;
                y = 250 - static_cast<int>(phase * 70.0f);
            }
            const int size = 6 + (i % 3) * 4;
            lv_obj_set_pos(ui.particles[i], x, y);
            lv_obj_set_size(ui.particles[i], size, size);
            lv_obj_set_style_bg_color(ui.particles[i], color(colors[i]), 0);
            lv_obj_set_style_bg_opa(
                ui.particles[i],
                static_cast<lv_opa_t>(80 + (1.0f - phase) * 170.0f), 0);
            lv_obj_set_style_border_width(ui.particles[i], 0, 0);
        }
        return;
    }
    if (shown_theme == Pet::Beach) {
        for (int i = 0; i < 6; ++i) {
            const float phase = std::fmod(t * (0.16f + i * 0.015f) + i * 0.17f, 1.0f);
            const int x = 72 + i * 68 + static_cast<int>(std::sin(t * 0.7f + i) * 8.0f);
            const int y = 300 - static_cast<int>(phase * 245.0f);
            const int size = 7 + (i % 3) * 3;
            lv_obj_set_pos(ui.particles[i], x, y);
            lv_obj_set_size(ui.particles[i], size, size);
            lv_obj_set_style_bg_color(ui.particles[i], color(0xFFFFFF), 0);
            lv_obj_set_style_bg_opa(ui.particles[i], LV_OPA_20, 0);
            lv_obj_set_style_border_width(ui.particles[i], 2, 0);
            lv_obj_set_style_border_color(ui.particles[i], color(0x278C9A), 0);
            lv_obj_set_style_border_opa(ui.particles[i], LV_OPA_70, 0);
        }
        return;
    }
    for (int i = 0; i < 6; ++i) {
        lv_obj_set_style_bg_color(ui.particles[i], color(accent), 0);
        lv_obj_set_style_bg_opa(ui.particles[i], LV_OPA_TRANSP, 0);
        lv_obj_set_style_border_width(ui.particles[i], 0, 0);
    }

    if (state == Pet::Thinking || state == Pet::Searching) {
        const float speed = state == Pet::Searching ? 2.2f : 1.15f;
        for (int i = 0; i < 3; ++i) {
            const float angle = t * speed + i * 2.0f * Pi / 3.0f;
            lv_obj_set_pos(ui.particles[i],
                           252 + static_cast<int>(185.0f * std::cos(angle)),
                           160 + static_cast<int>(118.0f * std::sin(angle)));
            lv_obj_set_style_bg_opa(ui.particles[i], 100 + i * 35, 0);
        }
    } else if (state == Pet::Working) {
        for (int i = 0; i < 6; ++i) {
            const float angle = t * 2.8f + i * Pi / 3.0f;
            lv_obj_set_pos(ui.particles[i],
                           438 + static_cast<int>(18.0f * std::cos(angle)),
                           280 + static_cast<int>(18.0f * std::sin(angle)));
            lv_obj_set_style_bg_opa(ui.particles[i], 70 + i * 25, 0);
        }
    } else if (state == Pet::Done) {
        for (int i = 0; i < 6; ++i) {
            const float phase = std::fmod(t * 0.34f + i * 0.17f, 1.0f);
            const int side = i % 2 ? 1 : -1;
            lv_obj_set_pos(ui.particles[i],
                           255 + side * (145 + static_cast<int>(phase * 40)),
                           70 + static_cast<int>(phase * 215));
            lv_obj_set_style_bg_opa(ui.particles[i],
                                    static_cast<lv_opa_t>((1.0f - phase) * 220), 0);
        }
    } else if (state == Pet::Error) {
        lv_obj_set_pos(ui.particles[0], 447, 78);
        lv_obj_set_style_bg_opa(ui.particles[0],
                                static_cast<lv_opa_t>(130 + 80 * std::abs(std::sin(t * 5))), 0);
    } else if (state == Pet::Waiting) {
        for (int i = 0; i < 3; ++i) {
            const float phase = std::fmod(t * 0.75f + i * 0.28f, 1.0f);
            lv_obj_set_pos(ui.particles[i], 430 + i * 15,
                           92 - static_cast<int>(phase * 42.0f));
            lv_obj_set_style_bg_opa(
                ui.particles[i],
                static_cast<lv_opa_t>((1.0f - phase) * 220.0f), 0);
        }
    } else if (state == Pet::Idle || state == Pet::Paused ||
               state == Pet::Offline) {
        const float speed = state == Pet::Idle ? 0.18f : 0.10f;
        for (int i = 0; i < 4; ++i) {
            const float phase = std::fmod(t * speed + i * 0.23f, 1.0f);
            lv_obj_set_pos(ui.particles[i], 96 + i * 94,
                           286 - static_cast<int>(phase * 185.0f));
            lv_obj_set_style_bg_opa(
                ui.particles[i],
                static_cast<lv_opa_t>((1.0f - phase) *
                                      (state == Pet::Offline ? 55 : 110)), 0);
        }
    }
}

void animate_ui(lv_timer_t *)
{
    bool is_connected;
    const Pet::View view = get_view(&is_connected);
    const auto &picker_palette = theme_palette(active_theme(view.theme));
    if (ModelPicker::tick(esp_timer_get_time() / 1000, is_connected,
                          picker_palette.bg, picker_palette.main,
                          picker_palette.muted, picker_palette.input))
        request_theme_cycle();
    if (consume_theme_cycle()) {
#ifndef PET_DESKTOP_PREVIEW
        CelestialDevice::cycle_theme();
        const Pet::Theme next = active_theme(view.theme);
#else
        const Pet::Theme current = active_theme(view.theme);
        const Pet::Theme next = static_cast<Pet::Theme>(
            (static_cast<unsigned>(current) + 1U) % Pet::ThemeCount);
        activate_local_theme(next);
#endif
        update_static_state(view, is_connected);
        DeviceLink::printf("@THEME source=button theme=%s\n", theme_name(next));
    }
    const Pet::Theme theme = active_theme(view.theme);
    if (view.state != shown_state || theme != shown_theme ||
        is_connected != shown_connected ||
        view.balance_quota != shown_balance_quota ||
        view.spent_quota != shown_spent_quota ||
        view.request_count != shown_request_count ||
        view.today_request_count != shown_today_request_count ||
        view.today_quota != shown_today_quota ||
        view.today_prompt_tokens != shown_today_prompt_tokens ||
        view.today_completion_tokens != shown_today_completion_tokens ||
        view.input_tps_x10 != shown_input_tps_x10 ||
        view.output_tps_x10 != shown_output_tps_x10 ||
        view.quota_per_unit != shown_quota_per_unit ||
        view.usage_stale != shown_usage_stale ||
        account_hash(view) != shown_account_hash ||
        view.duration_seconds != shown_duration_seconds ||
        model_hash(view) != shown_models_hash ||
        labels_hash(view) != shown_labels_hash) {
        update_static_state(view, is_connected);
    }

#ifndef PET_DESKTOP_PREVIEW
    CelestialDevice::set_api_mode(view.api_mode);
    if (ModelPicker::visible()) CelestialDevice::dismiss_menu();
    CelestialDevice::tick(ui.stage, !detail_target && detail_progress == 0.0f);
#endif
    const int64_t now_us = esp_timer_get_time();
    const float t = now_us / 1000000.0f;
    float dt = last_anim_us ? (now_us - last_anim_us) / 1000000.0f : 0.016f;
    last_anim_us = now_us;
    dt = std::clamp(dt, 0.005f, 0.10f);

    if (title_transition_active) {
        title_transition_progress +=
            (1.0f - title_transition_progress) *
            (1.0f - std::exp(-dt * 10.0f));
        if (title_transition_progress > 0.995f) {
            finish_title_transition(theme);
        } else {
            const float eased =
                1.0f - std::pow(1.0f - title_transition_progress, 3.0f);
            lv_obj_set_x(ui.session_title,
                         220 - static_cast<int>(120.0f * eased));
            lv_obj_set_x(ui.next_session_title,
                         460 - static_cast<int>(240.0f * eased));
            lv_obj_set_style_opa(
                ui.session_title,
                static_cast<lv_opa_t>(255.0f * (1.0f - eased)), 0);
            lv_obj_set_style_opa(
                ui.next_session_title,
                static_cast<lv_opa_t>(32.0f + 223.0f * eased), 0);
        }
    }

    if (!last_chart_sample_us || now_us - last_chart_sample_us >= 1000000) {
        last_chart_sample_us = now_us;
        const int32_t input = view.input_tps_x10 == UINT32_MAX
                                  ? 0
                                  : static_cast<int32_t>(
                                        std::min<uint32_t>(view.input_tps_x10,
                                                           INT32_MAX));
        const int32_t output = view.output_tps_x10 == UINT32_MAX
                                   ? 0
                                   : static_cast<int32_t>(
                                         std::min<uint32_t>(view.output_tps_x10,
                                                            INT32_MAX));
        lv_chart_set_next_value(ui.detail_chart, ui.input_series, input);
        lv_chart_set_next_value(ui.detail_chart, ui.output_series, output);
        const int32_t peak = std::max<int32_t>({100, input, output});
        if (peak > chart_range_x10) {
            chart_range_x10 =
                static_cast<int32_t>(
                    ((static_cast<int64_t>(peak) * 12 / 10 + 99) / 100) * 100);
            lv_chart_set_range(ui.detail_chart, LV_CHART_AXIS_PRIMARY_Y,
                               0, chart_range_x10);
        }
    }

    const float page_target = detail_target ? 1.0f : 0.0f;
    detail_progress +=
        (page_target - detail_progress) * (1.0f - std::exp(-dt * 14.0f));
    if (std::abs(page_target - detail_progress) < 0.002f) {
        detail_progress = page_target;
    }
    lv_obj_set_x(ui.detail_page,
                 static_cast<int>((1.0f - detail_progress) * ScreenWidth));
    if (!detail_target && detail_progress == 0.0f) {
        lv_obj_add_flag(ui.detail_page, LV_OBJ_FLAG_HIDDEN);
    }

    if (detail_progress > 0.995f) {
        ++frame_count;
        return;
    }

#ifndef PET_DESKTOP_PREVIEW
    if (CelestialDevice::active()) {
        ++frame_count;
        return;
    }
#endif
    const float transition_seconds =
        std::clamp(view.transition_ms / 1000.0f, 0.20f, 2.0f);
    const float amount = 1.0f - std::exp(-dt * 5.0f / transition_seconds);
    const Pet::State state = view.state < Pet::StateCount ? view.state : Pet::Offline;
    if (Character::supports(theme)) {
        Character::animate(state, dt, transition_seconds, touch_energy);
        lv_obj_set_style_bg_color(ui.state_dot, color(
            state == Pet::Error ? 0xD75244 :
            state == Pet::Done ? 0x168B78 :
            theme == Pet::Mechanical ? 0xE6533D :
            theme == Pet::Paper ? 0x275AAF : 0x00876D), 0);
        ++frame_count;
        return;
    }
    if (theme == Pet::VanGogh) {
        update_vangogh_features(state, t, dt);
        lv_obj_set_style_bg_color(
            ui.state_dot, color(state == Pet::Done ? 0xDCC66E : 0x86B9C7), 0);
        ++frame_count;
        return;
    }
    FacePose target = pose_for(state, t);
    if (theme == Pet::Beach) {
        target.eye_width += 12.0f;
        target.eye_height = std::max(46.0f, target.eye_height - 10.0f);
        target.mouth_width += 10.0f;
        target.cheek = std::max(target.cheek, 0.28f);
        switch (state) {
        case Pet::Thinking:
            target.eye_open = 0.42f;
            target.eye_width = 72.0f;
            target.gaze_y = -14.0f;
            target.brow_tilt = -12.0f;
            target.mouth_curve = 1.0f;
            target.mouth_width = 42.0f;
            target.cheek = 0.10f;
            break;
        case Pet::Working:
            target.eye_open = 0.58f;
            target.gaze_x = 15.0f * std::sin(t * 4.0f);
            target.brow_tilt = 11.0f;
            target.mouth_curve = -7.0f;
            target.mouth_width = 70.0f;
            target.cheek = 0.22f;
            break;
        case Pet::Waiting:
            target.eye_open = 1.25f;
            target.eye_width = 98.0f;
            target.brow_tilt = -13.0f;
            target.brow_y = -8.0f;
            target.mouth_curve = 14.0f;
            target.mouth_width = 58.0f;
            target.cheek = 0.35f;
            break;
        case Pet::Done:
            target.eye_open = 0.10f;
            target.eye_width = 104.0f;
            target.mouth_curve = 28.0f;
            target.mouth_width = 108.0f;
            target.mouth_y = 187.0f;
            target.cheek = 1.0f;
            break;
        case Pet::Error:
            target.eye_open = 0.96f;
            target.gaze_y = 12.0f;
            target.brow_tilt = -18.0f;
            target.mouth_curve = -22.0f;
            target.mouth_width = 90.0f;
            target.mouth_y = 207.0f;
            target.cheek = 0.05f;
            break;
        case Pet::Paused:
            target.eye_open = 0.30f;
            target.gaze_y = 9.0f;
            target.brow_tilt = 13.0f;
            target.mouth_curve = -5.0f;
            target.mouth_width = 58.0f;
            target.cheek = 0.05f;
            break;
        case Pet::Offline:
            target.eye_open = 0.03f;
            target.brow_y = 8.0f;
            target.mouth_curve = -2.0f;
            target.mouth_width = 52.0f;
            target.cheek = 0.0f;
            break;
        case Pet::Searching:
            target.eye_open = 0.95f;
            target.eye_width = 100.0f;
            target.gaze_x = 22.0f * std::sin(t * 3.2f);
            target.brow_tilt = 8.0f;
            target.mouth_curve = 10.0f;
            target.mouth_width = 76.0f;
            target.cheek = 0.30f;
            break;
        default:
            break;
        }
    } else if (theme == Pet::Pixel) {
        target.eye_width = std::round((target.eye_width + 6.0f) / 8.0f) * 8.0f;
        target.eye_height = 64.0f;
        target.eye_open = std::round(target.eye_open * 4.0f) / 4.0f;
        target.gaze_x = std::round(target.gaze_x / 6.0f) * 6.0f;
        target.gaze_y = std::round(target.gaze_y / 5.0f) * 5.0f;
        target.brow_tilt = std::round(target.brow_tilt / 4.0f) * 4.0f;
        target.brow_y = std::round(target.brow_y / 3.0f) * 3.0f;
        target.mouth_curve = std::round(target.mouth_curve / 5.0f) * 5.0f;
        target.mouth_width = std::round(target.mouth_width / 8.0f) * 8.0f;
        target.mouth_y = std::round(target.mouth_y / 4.0f) * 4.0f;
    }
    blend_pose(animated_pose, target, amount);
    const float beach_target = theme == Pet::Beach ? 1.0f : 0.0f;
    beach_shape += (beach_target - beach_shape) * (1.0f - std::exp(-dt * 12.0f));
    int motion_x = 0;
    int motion_y = 0;
    switch (state) {
    case Pet::Working:
        motion_y = static_cast<int>(std::sin(t * 5.2f) * 3.0f);
        break;
    case Pet::Waiting:
        motion_y = -static_cast<int>((std::sin(t * 3.4f) + 1.0f) * 2.0f);
        break;
    case Pet::Done:
        motion_y = -static_cast<int>(std::abs(std::sin(t * 2.3f)) * 8.0f);
        break;
    case Pet::Error:
        motion_x = static_cast<int>(std::sin(t * 14.0f) * 5.0f);
        break;
    case Pet::Paused:
        motion_y = static_cast<int>((std::sin(t * 1.1f) + 1.0f) * 3.0f);
        break;
    case Pet::Searching:
        motion_x = static_cast<int>(std::sin(t * 2.7f) * 4.0f);
        break;
    default:
        motion_y = static_cast<int>(std::sin(t * 1.4f) * 2.0f);
        break;
    }
    if (theme == Pet::Pixel) {
        motion_x = (motion_x / 3) * 3;
        motion_y = (motion_y / 3) * 3;
    }
    const int face_x = 66 - static_cast<int>(18.0f * beach_shape) + motion_x;
    const int face_y = 48 - static_cast<int>(10.0f * beach_shape) + motion_y;
    const int face_w = 388 + static_cast<int>(36.0f * beach_shape);
    const int face_h = 254 + static_cast<int>(20.0f * beach_shape);
    lv_obj_set_pos(ui.face, face_x, face_y);
    lv_obj_set_size(ui.face, face_w, face_h);
    lv_obj_set_size(ui.face_inner, face_w - 20, face_h - 20);

    constexpr uint32_t PixelCycle[] = {
        0x43F5B5, 0x4DDCFF, 0x6C4BFF, 0xFF5FA2, 0xFFF36D, 0xFF8A4D,
    };
    const uint32_t target_accent =
        theme == Pet::Pixel
            ? PixelCycle[(static_cast<unsigned>(t * 3.0f) + state) %
                         (sizeof(PixelCycle) / sizeof(PixelCycle[0]))]
            : StateColors[state];
    approach(accent_r, static_cast<float>((target_accent >> 16) & 255), amount);
    approach(accent_g, static_cast<float>((target_accent >> 8) & 255), amount);
    approach(accent_b, static_cast<float>(target_accent & 255), amount);
    const uint32_t accent = (static_cast<uint32_t>(accent_r) << 16) |
                            (static_cast<uint32_t>(accent_g) << 8) |
                            static_cast<uint32_t>(accent_b);
    update_eye(ui.eye_left, ui.eye_glow_left, ui.pupil_left, ui.glint_left,
               126 + static_cast<int>(4.0f * beach_shape),
               123 + static_cast<int>(4.0f * beach_shape), animated_pose, theme,
               accent);
    update_eye(ui.eye_right, ui.eye_glow_right, ui.pupil_right, ui.glint_right,
               278 + static_cast<int>(12.0f * beach_shape),
               123 + static_cast<int>(4.0f * beach_shape), animated_pose, theme,
               accent);
    update_lines(animated_pose, accent);
    if (theme == Pet::Pixel) {
        update_pixel_features(animated_pose, state, t, accent);
    }
    update_particles(state, t, accent);

    const float breathe = (std::sin(t * 1.7f) + 1.0f) * 0.5f;
    if (theme != Pet::Pixel && theme != Pet::VanGogh) {
        lv_obj_set_style_bg_color(
            ui.aura_outer,
            color(theme == Pet::Beach ? 0x9FE7E4 :
                  accent), 0);
        lv_obj_set_style_bg_color(
            ui.aura_inner,
            color(theme == Pet::Beach ? 0xDFF5E8 :
                  accent), 0);
        lv_obj_set_style_bg_opa(
            ui.aura_outer,
            theme == Pet::Beach ? static_cast<lv_opa_t>(LV_OPA_COVER) :
            static_cast<lv_opa_t>(18 + breathe * 16 + touch_energy * 16), 0);
        lv_obj_set_style_bg_opa(
            ui.aura_inner,
            theme == Pet::Beach ? static_cast<lv_opa_t>(LV_OPA_COVER) :
            static_cast<lv_opa_t>(24 + breathe * 20 + touch_energy * 24), 0);
        lv_obj_set_style_border_color(
            ui.face, color(theme == Pet::Beach
                               ? mix_rgb(0x278C9A, accent, 0.22f)
                               : accent), 0);
        lv_obj_set_style_border_opa(
            ui.face,
            theme == Pet::Beach ? static_cast<lv_opa_t>(LV_OPA_80) :
            static_cast<lv_opa_t>(105 + breathe * 55 + touch_energy * 55), 0);
        lv_obj_set_style_bg_color(ui.eye_glow_left, color(accent), 0);
        lv_obj_set_style_bg_color(ui.eye_glow_right, color(accent), 0);
        const bool eyes_closed =
            animated_pose.eye_height * animated_pose.eye_open <= 6.5f;
        const lv_opa_t eye_glow_opacity = eyes_closed
            ? static_cast<lv_opa_t>(LV_OPA_TRANSP)
            : static_cast<lv_opa_t>(24 + animated_pose.cheek * 22);
        lv_obj_set_style_bg_opa(ui.eye_glow_left, eye_glow_opacity, 0);
        lv_obj_set_style_bg_opa(ui.eye_glow_right, eye_glow_opacity, 0);
        const uint32_t cheek_color =
            theme == Pet::Beach ? 0xF2C94C : accent;
        lv_obj_set_style_bg_color(ui.cheek_left, color(cheek_color), 0);
        lv_obj_set_style_bg_color(ui.cheek_right, color(cheek_color), 0);
        lv_obj_set_style_bg_opa(
            ui.cheek_left, static_cast<lv_opa_t>(animated_pose.cheek * 75), 0);
        lv_obj_set_style_bg_opa(
            ui.cheek_right, static_cast<lv_opa_t>(animated_pose.cheek * 75), 0);
        lv_obj_set_style_bg_color(ui.state_dot, color(accent), 0);
        lv_obj_set_style_text_color(ui.effect, color(accent), 0);
    } else if (theme == Pet::VanGogh) {
        const bool eyes_closed =
            animated_pose.eye_height * animated_pose.eye_open <= 13.5f;
        const lv_opa_t halo_opacity = eyes_closed
            ? static_cast<lv_opa_t>(LV_OPA_TRANSP)
            : static_cast<lv_opa_t>(
                  58 + 38 * ((std::sin(t * 1.35f) + 1.0f) * 0.5f));
        const lv_color_t halo_color =
            color(mix_rgb(0x8CAAA6, accent, 0.16f));
        lv_obj_set_style_border_color(ui.eye_glow_left, halo_color, 0);
        lv_obj_set_style_border_color(ui.eye_glow_right, halo_color, 0);
        lv_obj_set_style_border_opa(ui.eye_glow_left, halo_opacity, 0);
        lv_obj_set_style_border_opa(ui.eye_glow_right, halo_opacity, 0);
        lv_obj_set_style_bg_opa(ui.eye_glow_left, LV_OPA_TRANSP, 0);
        lv_obj_set_style_bg_opa(ui.eye_glow_right, LV_OPA_TRANSP, 0);
        lv_obj_set_style_bg_opa(ui.aura_outer, LV_OPA_TRANSP, 0);
        lv_obj_set_style_bg_opa(ui.aura_inner, LV_OPA_TRANSP, 0);
        lv_obj_set_style_border_opa(ui.face, LV_OPA_TRANSP, 0);
        lv_obj_set_style_bg_opa(ui.cheek_left, LV_OPA_TRANSP, 0);
        lv_obj_set_style_bg_opa(ui.cheek_right, LV_OPA_TRANSP, 0);
        lv_obj_set_style_bg_color(ui.state_dot, color(accent), 0);
    }
    lv_obj_set_style_opa(ui.effect,
                         static_cast<lv_opa_t>(170 + 60 * std::abs(std::sin(t * 2.2f))), 0);
    ++frame_count;
}

void create_ui()
{
    ui.screen = lv_screen_active();
    lv_obj_remove_style_all(ui.screen);
    lv_obj_set_style_bg_color(ui.screen, color(0x071019), 0);
    lv_obj_set_style_bg_opa(ui.screen, LV_OPA_COVER, 0);
    lv_obj_clear_flag(ui.screen, LV_OBJ_FLAG_SCROLLABLE);

    ui.header_title = main_label(ui.screen, "Codex助手", &font_cn_30);
    lv_obj_set_pos(ui.header_title, 24, 14);
    ui.session_title = lv_canvas_create(ui.screen);
    lv_canvas_set_buffer(ui.session_title, session_title_buffer,
                         Pet::TextWidth, 24, LV_COLOR_FORMAT_RGB565);
    lv_image_set_inner_align(ui.session_title, LV_IMAGE_ALIGN_TOP_LEFT);
    lv_obj_set_pos(ui.session_title, 220, 19);
    ui.next_session_title = lv_canvas_create(ui.screen);
    lv_canvas_set_buffer(ui.next_session_title, next_session_title_buffer,
                         Pet::TextWidth, 24, LV_COLOR_FORMAT_RGB565);
    lv_image_set_inner_align(ui.next_session_title, LV_IMAGE_ALIGN_TOP_LEFT);
    lv_obj_set_pos(ui.next_session_title, 460, 19);
    lv_obj_set_width(ui.next_session_title, 150);

    ui.connection = make_box(ui.screen, 674, 13, 102, 38, 8, 0x10202C);
    lv_obj_set_style_border_width(ui.connection, 1, 0);
    lv_obj_set_style_border_color(ui.connection, color(0x203342), 0);
    ui.connection_dot = make_box(ui.connection, 12, 14, 10, 10, LV_RADIUS_CIRCLE, 0x66778C);
    ui.connection_text = muted_label(ui.connection, "未连接", &font_cn_18);
    lv_obj_set_pos(ui.connection_text, 29, 8);

    ui.top_rule = make_box(ui.screen, 24, 62, 754, 1, 0, 0x203342);
    ui.beach_sky = make_box(ui.screen, 0, 63, ScreenWidth, 218, 0, 0xFFF8D9);
    ui.beach_horizon = make_box(ui.screen, 0, 281, ScreenWidth, 101, 0, 0x9FE7E4);
    ui.beach_sand = make_box(ui.screen, 0, 382, ScreenWidth, 98, 0, 0xF2C94C);
    constexpr uint32_t pixel_colors[6] = {
        0x43F5B5, 0x4DDCFF, 0x6C4BFF, 0xFF5FA2, 0xFFF36D, 0xFF8A4D,
    };
    for (int i = 0; i < 6; ++i) {
        ui.pixel_top_strip[i] =
            make_box(ui.screen, i * 134, 0, i == 5 ? 130 : 134, 7,
                     0, pixel_colors[i], LV_OPA_TRANSP);
    }
    ui.stage = make_box(ui.screen, 20, 78, 520, 350, 8, 0x0A1722);
    ui.vangogh_scene = lv_image_create(ui.stage);
    lv_image_set_src(ui.vangogh_scene, &VanGogh::Assets::scene);
    lv_obj_set_pos(ui.vangogh_scene, 0, 0);
    lv_obj_add_flag(ui.vangogh_scene, LV_OBJ_FLAG_HIDDEN);
    for (int line = 0; line < 8; ++line) {
        ui.vangogh_swirl[line] = lv_line_create(ui.stage);
        lv_obj_set_style_line_width(ui.vangogh_swirl[line], 2, 0);
        lv_obj_set_style_line_rounded(ui.vangogh_swirl[line], true, 0);
        lv_obj_set_style_line_color(
            ui.vangogh_swirl[line], color(0x6A9FB7), 0);
        lv_obj_set_style_line_opa(
            ui.vangogh_swirl[line], LV_OPA_TRANSP, 0);
        for (int i = 0; i < 10; ++i) {
            vangogh_swirl_points[line][i] = {
                static_cast<lv_value_precise_t>(260 + i * 3),
                static_cast<lv_value_precise_t>(130 + line * 8),
            };
        }
        lv_line_set_points(
            ui.vangogh_swirl[line], vangogh_swirl_points[line], 10);
    }
    for (int line = 0; line < 4; ++line) {
        ui.vangogh_tree[line] = lv_line_create(ui.stage);
        lv_obj_set_style_line_width(ui.vangogh_tree[line], 2, 0);
        lv_obj_set_style_line_opa(ui.vangogh_tree[line], LV_OPA_TRANSP, 0);
        for (int i = 0; i < 7; ++i) {
            vangogh_tree_points[line][i] = {
                static_cast<lv_value_precise_t>(100 + line * 8),
                static_cast<lv_value_precise_t>(30 + i * 25),
            };
        }
        lv_line_set_points(ui.vangogh_tree[line],
                           vangogh_tree_points[line], 7);
    }
    for (int line = 0; line < 4; ++line) {
        ui.vangogh_flow[line] = lv_line_create(ui.stage);
        lv_obj_set_style_line_width(ui.vangogh_flow[line], line < 2 ? 2 : 1, 0);
        lv_obj_set_style_line_rounded(ui.vangogh_flow[line], true, 0);
        lv_obj_set_style_line_color(ui.vangogh_flow[line], color(0x6A9FB7), 0);
        lv_obj_set_style_line_opa(ui.vangogh_flow[line], LV_OPA_TRANSP, 0);
        for (int i = 0; i < 6; ++i) {
            vangogh_flow_points[line][i] = {
                static_cast<lv_value_precise_t>(260 + i * 3),
                static_cast<lv_value_precise_t>(130 + line * 4),
            };
        }
        lv_line_set_points(ui.vangogh_flow[line], vangogh_flow_points[line], 6);
    }
    for (int i = 0; i < 8; ++i) {
        ui.pixel_grid_v[i] =
            make_box(ui.stage, 56 + i * 58, 18, 1, 326, 0,
                     0x282852, LV_OPA_TRANSP);
    }
    for (int i = 0; i < 5; ++i) {
        ui.pixel_grid_h[i] =
            make_box(ui.stage, 18, 62 + i * 58, 502, 1, 0,
                     0x282852, LV_OPA_TRANSP);
    }
    const int corner_geometry[8][4] = {
        {12, 12, 38, 4}, {12, 12, 4, 30},
        {488, 12, 38, 4}, {522, 12, 4, 30},
        {12, 342, 38, 4}, {12, 316, 4, 30},
        {488, 342, 38, 4}, {522, 316, 4, 30},
    };
    for (int i = 0; i < 8; ++i) {
        ui.pixel_corner[i] =
            make_box(ui.stage, corner_geometry[i][0], corner_geometry[i][1],
                     corner_geometry[i][2], corner_geometry[i][3], 0,
                     i < 4 ? 0x43F5B5 : 0xFF5FA2, LV_OPA_TRANSP);
    }
    ui.pixel_scanline =
        make_box(ui.stage, 18, 32, 502, 4, 0, 0x4DDCFF, LV_OPA_TRANSP);
    ui.aura_outer = make_box(ui.stage, 42, 26, 436, 298, 78, StateColors[Pet::Offline], 28);
    ui.aura_inner = make_box(ui.stage, 56, 39, 408, 272, 70, StateColors[Pet::Offline], 36);
    ui.face = make_box(ui.stage, 66, 48, 388, 254, 64, 0x172837);
    lv_obj_set_style_border_width(ui.face, 2, 0);
    lv_obj_set_style_border_color(ui.face, color(StateColors[Pet::Offline]), 0);
    lv_obj_set_style_border_opa(ui.face, 130, 0);
    ui.face_inner = make_box(ui.face, 10, 10, 368, 234, 56, 0x0D1B28);

    ui.eye_glow_left = make_box(ui.face_inner, 77, 77, 98, 82, 40,
                                StateColors[Pet::Offline], 34);
    ui.eye_glow_right = make_box(ui.face_inner, 229, 77, 98, 82, 40,
                                 StateColors[Pet::Offline], 34);
    ui.eye_left = make_box(ui.face_inner, 84, 89, 84, 68, 34, 0xE9FBFF);
    ui.eye_right = make_box(ui.face_inner, 236, 89, 84, 68, 34, 0xE9FBFF);
    ui.pupil_left = make_box(ui.eye_left, 29, 13, 27, 42, 15, 0x173444);
    ui.pupil_right = make_box(ui.eye_right, 29, 13, 27, 42, 15, 0x173444);
    ui.glint_left = make_box(ui.pupil_left, 5, 6, 7, 10, LV_RADIUS_CIRCLE, 0xFFFFFF, LV_OPA_80);
    ui.glint_right = make_box(ui.pupil_right, 5, 6, 7, 10, LV_RADIUS_CIRCLE, 0xFFFFFF, LV_OPA_80);

    ui.brow_left = lv_line_create(ui.face_inner);
    ui.brow_right = lv_line_create(ui.face_inner);
    for (lv_obj_t *brow : {ui.brow_left, ui.brow_right}) {
        lv_obj_set_style_line_width(brow, 5, 0);
        lv_obj_set_style_line_rounded(brow, true, 0);
    }
    ui.mouth = lv_line_create(ui.face_inner);
    lv_obj_set_style_line_width(ui.mouth, 6, 0);
    lv_obj_set_style_line_rounded(ui.mouth, true, 0);
    ui.cheek_left = make_box(ui.face_inner, 57, 171, 46, 13, LV_RADIUS_CIRCLE,
                             StateColors[Pet::Offline], 20);
    ui.cheek_right = make_box(ui.face_inner, 301, 171, 46, 13, LV_RADIUS_CIRCLE,
                              StateColors[Pet::Offline], 20);

    ui.pixel_ear_left =
        make_box(ui.stage, 49, 126, 26, 82, 0, 0x6C4BFF, LV_OPA_TRANSP);
    ui.pixel_ear_right =
        make_box(ui.stage, 455, 126, 26, 82, 0, 0x6C4BFF, LV_OPA_TRANSP);
    for (int i = 0; i < 3; ++i) {
        ui.pixel_brow_left[i] =
            make_box(ui.face_inner, 78 + i * 19, 52, 17, 8, 0,
                     0x43F5B5, LV_OPA_TRANSP);
        ui.pixel_brow_right[i] =
            make_box(ui.face_inner, 270 + i * 19, 52, 17, 8, 0,
                     0x43F5B5, LV_OPA_TRANSP);
    }
    for (int i = 0; i < 5; ++i) {
        ui.pixel_mouth[i] =
            make_box(ui.face_inner, 158 + i * 20, 194, 18, 10, 0,
                     0xFF5FA2, LV_OPA_TRANSP);
    }
    for (int i = 0; i < 8; ++i) {
        ui.pixel_meter[i] =
            make_box(ui.stage, 168 + i * 25, 337, 20, 9, 0,
                     pixel_colors[i % 6], LV_OPA_TRANSP);
    }
    ui.vangogh_moon = lv_image_create(ui.stage);
    lv_image_set_src(ui.vangogh_moon, &VanGogh::Assets::moon);
    lv_obj_set_pos(ui.vangogh_moon, 426, 0);
    for (int i = 0; i < 12; ++i) {
        ui.vangogh_star[i] = lv_image_create(ui.stage);
        lv_image_set_src(ui.vangogh_star[i], &VanGogh::Assets::star);
        lv_obj_set_pos(ui.vangogh_star[i], 0, 0);
        lv_obj_set_style_opa(ui.vangogh_star[i], LV_OPA_TRANSP, 0);
    }
    constexpr int vangogh_windows[6][4] = {
        {220, 333, 3, 3}, {294, 337, 3, 3}, {366, 335, 4, 3},
        {415, 340, 3, 3}, {470, 326, 3, 4}, {502, 342, 4, 3},
    };
    for (int i = 0; i < 6; ++i) {
        ui.vangogh_window[i] =
            make_box(ui.stage,
                     vangogh_windows[i][0], vangogh_windows[i][1],
                     vangogh_windows[i][2], vangogh_windows[i][3], 0,
                     0xF3C95D, LV_OPA_TRANSP);
    }

    ui.effect = make_label(ui.stage, "Z", &lv_font_montserrat_32,
                           StateColors[Pet::Offline], false);
    lv_obj_set_pos(ui.effect, 444, 38);
    for (int i = 0; i < 6; ++i) {
        const int size = 6 + (i % 3) * 2;
        ui.particles[i] = make_box(ui.stage, 0, 0, size, size,
                                   LV_RADIUS_CIRCLE, StateColors[Pet::Offline],
                                   LV_OPA_TRANSP);
    }
    ui.character = Character::create(ui.stage);

    ui.data_panel = make_box(ui.screen, 566, 74, 218, 378, 8, 0x10202C);
    lv_obj_set_style_border_width(ui.data_panel, 1, 0);
    lv_obj_set_style_border_color(ui.data_panel, color(0x203342), 0);
    ui.side_rule = make_box(ui.screen, 557, 78, 1, 350, 0, 0x203342);
    ui.state_caption = muted_label(ui.screen, "当前状态", &font_cn_18);
    lv_obj_set_pos(ui.state_caption, 580, 82);
    set_pixel_text_size(ui.state_caption, 24);
    ui.state_dot = make_box(ui.screen, 580, 121, 11, 11,
                            LV_RADIUS_CIRCLE, StateColors[Pet::Offline]);
    ui.state_name = main_label(ui.screen, "未连接", &font_cn_30);
    lv_obj_set_pos(ui.state_name, 604, 106);
    ui.state_detail = muted_label(ui.screen, StateDetails[Pet::Offline], &font_cn_18);
    lv_obj_set_pos(ui.state_detail, 580, 154);
    lv_obj_set_width(ui.state_detail, 196);
    lv_label_set_long_mode(ui.state_detail, LV_LABEL_LONG_WRAP);
    ui.session_duration = muted_label(ui.screen, "TIME 00:00:00", &lv_font_montserrat_14);
    lv_obj_set_pos(ui.session_duration, 580, 174);

    ui.metric_rule = make_box(ui.screen, 580, 194, 196, 1, 0, 0x203342);
    ui.api_caption = muted_label(ui.screen, "MoreCode · 余额", &font_cn_18);
    lv_obj_set_pos(ui.api_caption, 580, 204);
    ui.api_hint = make_label(ui.screen, LV_SYMBOL_RIGHT, &lv_font_montserrat_18,
                             0x39D8C5, false);
    lv_obj_set_pos(ui.api_hint, 754, 205);
    ui.balance_value = main_label(ui.screen, "--", &lv_font_montserrat_32);
    lv_obj_set_pos(ui.balance_value, 580, 227);
    lv_label_set_long_mode(ui.balance_value, LV_LABEL_LONG_DOT);
    ui.quota_ring = lv_arc_create(ui.screen);
    lv_obj_remove_style_all(ui.quota_ring);
    lv_arc_set_range(ui.quota_ring, 0, 1000);
    lv_arc_set_bg_angles(ui.quota_ring, 0, 360);
    lv_arc_set_rotation(ui.quota_ring, 270);
    lv_arc_set_value(ui.quota_ring, 0);
    lv_obj_set_style_arc_rounded(ui.quota_ring, true, LV_PART_INDICATOR);
    lv_obj_remove_flag(ui.quota_ring, LV_OBJ_FLAG_CLICKABLE);
    ui.quota_percent = main_label(ui.screen, "--", &lv_font_montserrat_28);
    ui.account_plan = main_label(ui.screen, "--", &font_menu_18);
    ui.account_expiry = muted_label(ui.screen, "", &font_menu_18);
    ui.account_reset = muted_label(ui.screen, "", &font_menu_18);

    ui.models_caption = muted_label(ui.screen, "今日模型", &font_cn_18);
    lv_obj_set_pos(ui.models_caption, 580, 270);
    set_pixel_text_size(ui.models_caption, 24);
    for (unsigned i = 0; i < Pet::ModelCount; ++i) {
        const int y = 304 + static_cast<int>(i) * 38;
        ui.model_name[i] = main_label(ui.screen, "-", &lv_font_montserrat_14);
        lv_obj_set_pos(ui.model_name[i], 580, y);
        lv_obj_set_width(ui.model_name[i], 124);
        lv_label_set_long_mode(ui.model_name[i], LV_LABEL_LONG_DOT);
        ui.model_cost[i] = main_label(ui.screen, "--", &lv_font_montserrat_14);
        lv_obj_set_width(ui.model_cost[i], 68);
        lv_obj_set_style_text_align(ui.model_cost[i], LV_TEXT_ALIGN_RIGHT, 0);
        lv_obj_set_pos(ui.model_cost[i], 708, y);
    }

    ui.input_rate = make_label(ui.screen, "-- Token/s", &lv_font_montserrat_16, 0x39D8C5);
    lv_label_set_long_mode(ui.input_rate, LV_LABEL_LONG_DOT);
    lv_obj_set_pos(ui.input_rate, 580, 399);
    set_pixel_text_size(ui.input_rate, 24);
    ui.input_arrow = make_label(ui.screen, LV_SYMBOL_UP, &lv_font_montserrat_16,
                                0x39D8C5, false);
    lv_obj_set_pos(ui.input_arrow, 756, 399);
    ui.output_rate = make_label(ui.screen, "-- Token/s", &lv_font_montserrat_16, 0xFF8A78);
    lv_label_set_long_mode(ui.output_rate, LV_LABEL_LONG_DOT);
    lv_obj_set_pos(ui.output_rate, 580, 424);
    set_pixel_text_size(ui.output_rate, 24);
    ui.output_arrow = make_label(ui.screen, LV_SYMBOL_DOWN, &lv_font_montserrat_16,
                                 0xFF8A78, false);
    lv_obj_set_pos(ui.output_arrow, 756, 424);

    ui.detail_page = make_box(ui.screen, ScreenWidth, 0, ScreenWidth, ScreenHeight,
                              0, 0x071019);
    ui.detail_back = make_label(ui.detail_page, LV_SYMBOL_LEFT,
                                &lv_font_montserrat_24, 0x39D8C5, false);
    lv_obj_set_pos(ui.detail_back, 24, 20);
    lv_obj_t *detail_title = main_label(ui.detail_page, "今日统计", &font_cn_30);
    ui.detail_title = detail_title;
    lv_obj_set_pos(detail_title, 62, 13);
    lv_obj_t *detail_subtitle =
        muted_label(ui.detail_page, "MoreCode API", &lv_font_montserrat_16);
    ui.detail_subtitle = detail_subtitle;
    lv_obj_set_pos(detail_subtitle, 197, 23);
    ui.detail_freshness = muted_label(ui.detail_page, "缓存数据", &font_cn_18);
    lv_obj_set_width(ui.detail_freshness, 140);
    lv_obj_set_style_text_align(ui.detail_freshness, LV_TEXT_ALIGN_RIGHT, 0);
    lv_obj_set_pos(ui.detail_freshness, 636, 20);
    ui.detail_rule = make_box(ui.detail_page, 24, 59, 752, 1, 0, 0x203342);

    const char *card_titles[5] = {
        "今日消费", "今日请求", "Input Token", "Output Token", "历史消费",
    };
    for (int i = 0; i < 5; ++i) {
        const int x = 24 + i * 152;
        ui.detail_cards[i] = make_box(ui.detail_page, x, 76, 140, 78,
                                      8, 0x0A1722);
        lv_obj_set_style_border_width(ui.detail_cards[i], 1, 0);
        lv_obj_set_style_border_color(ui.detail_cards[i], color(0x203342), 0);
        lv_obj_t *title =
            muted_label(ui.detail_cards[i], card_titles[i], &font_cn_18);
        ui.detail_titles[i] = title;
        lv_obj_set_pos(title, 12, 8);
        ui.detail_values[i] =
            main_label(ui.detail_cards[i], "--", &lv_font_montserrat_22);
        lv_obj_set_pos(ui.detail_values[i], 12, 38);
        lv_obj_set_width(ui.detail_values[i], 116);
        lv_label_set_long_mode(ui.detail_values[i], LV_LABEL_LONG_DOT);
    }

    lv_obj_t *chart_title = main_label(ui.detail_page, "实时 Token 速率",
                                       &font_cn_18);
    ui.detail_chart_title = chart_title;
    lv_obj_set_pos(chart_title, 24, 170);
    ui.detail_input_rate =
        make_label(ui.detail_page, "-- Token/s", &lv_font_montserrat_14, 0x39D8C5);
    lv_obj_set_pos(ui.detail_input_rate, 236, 172);
    ui.detail_output_rate =
        make_label(ui.detail_page, "-- Token/s", &lv_font_montserrat_14, 0xFF8A78);
    lv_obj_set_pos(ui.detail_output_rate, 366, 172);

    ui.detail_chart = lv_chart_create(ui.detail_page);
    lv_obj_remove_style_all(ui.detail_chart);
    lv_obj_set_pos(ui.detail_chart, 24, 199);
    lv_obj_set_size(ui.detail_chart, 466, 244);
    lv_obj_set_style_radius(ui.detail_chart, 8, 0);
    lv_obj_set_style_bg_color(ui.detail_chart, color(0x0A1722), 0);
    lv_obj_set_style_bg_opa(ui.detail_chart, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(ui.detail_chart, 1, 0);
    lv_obj_set_style_border_color(ui.detail_chart, color(0x203342), 0);
    lv_obj_set_style_pad_all(ui.detail_chart, 14, 0);
    lv_obj_set_style_line_color(ui.detail_chart, color(0x203342), LV_PART_MAIN);
    lv_obj_set_style_line_opa(ui.detail_chart, LV_OPA_60, LV_PART_MAIN);
    lv_obj_set_style_line_width(ui.detail_chart, 1, LV_PART_MAIN);
    lv_obj_set_style_size(ui.detail_chart, 0, 0, LV_PART_INDICATOR);
    lv_obj_set_style_line_width(ui.detail_chart, 3, LV_PART_ITEMS);
    lv_chart_set_type(ui.detail_chart, LV_CHART_TYPE_LINE);
    lv_chart_set_update_mode(ui.detail_chart, LV_CHART_UPDATE_MODE_SHIFT);
    lv_chart_set_point_count(ui.detail_chart, 60);
    lv_chart_set_div_line_count(ui.detail_chart, 5, 6);
    lv_chart_set_range(ui.detail_chart, LV_CHART_AXIS_PRIMARY_Y, 0, chart_range_x10);
    ui.input_series =
        lv_chart_add_series(ui.detail_chart, color(0x39D8C5),
                            LV_CHART_AXIS_PRIMARY_Y);
    ui.output_series =
        lv_chart_add_series(ui.detail_chart, color(0xFF8A78),
                            LV_CHART_AXIS_PRIMARY_Y);
    lv_chart_set_all_value(ui.detail_chart, ui.input_series, 0);
    lv_chart_set_all_value(ui.detail_chart, ui.output_series, 0);

    lv_obj_t *model_title = main_label(ui.detail_page, "各模型消费", &font_cn_18);
    ui.detail_model_title = model_title;
    lv_obj_set_pos(model_title, 514, 170);
    for (unsigned i = 0; i < Pet::ModelCount; ++i) {
        const int y = 202 + static_cast<int>(i) * 60;
        ui.detail_model_name[i] =
            main_label(ui.detail_page, "暂无模型数据", &font_cn_18);
        lv_obj_set_pos(ui.detail_model_name[i], 514, y);
        lv_obj_set_width(ui.detail_model_name[i], 164);
        lv_label_set_long_mode(ui.detail_model_name[i], LV_LABEL_LONG_DOT);
        ui.detail_model_cost[i] =
            main_label(ui.detail_page, "--", &lv_font_montserrat_14);
        lv_obj_set_width(ui.detail_model_cost[i], 90);
        lv_obj_set_style_text_align(ui.detail_model_cost[i], LV_TEXT_ALIGN_RIGHT, 0);
        lv_obj_set_pos(ui.detail_model_cost[i], 686, y);
        ui.detail_model_tokens[i] =
            muted_label(ui.detail_page, "--", &font_cn_18);
        lv_obj_set_pos(ui.detail_model_tokens[i], 514, y + 19);
        ui.detail_model_track[i] =
            make_box(ui.detail_page, 514, y + 41, 230, 5,
                     LV_RADIUS_CIRCLE, 0x1C303F);
        ui.detail_model_fill[i] =
            make_box(ui.detail_model_track[i], 0, 0, 0, 5,
                     LV_RADIUS_CIRCLE, i == 0 ? 0x39D8C5 :
                     i == 1 ? 0x5AB7FF : i == 2 ? 0xA98AFF : 0xFFC857);
    }
    lv_obj_add_flag(ui.detail_page, LV_OBJ_FLAG_HIDDEN);

    ui.gesture_layer = lv_obj_create(ui.screen);
    lv_obj_remove_style_all(ui.gesture_layer);
    lv_obj_set_pos(ui.gesture_layer, 0, 0);
    lv_obj_set_size(ui.gesture_layer, ScreenWidth, ScreenHeight);
    lv_obj_set_style_bg_opa(ui.gesture_layer, LV_OPA_TRANSP, 0);
    lv_obj_add_flag(ui.gesture_layer, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_clear_flag(ui.gesture_layer, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(ui.gesture_layer, gesture_event, LV_EVENT_ALL, nullptr);

#ifndef PET_DESKTOP_PREVIEW
    CelestialDevice::init(ui.screen, ui.detail_page);
#endif
    ModelPicker::init(ui.screen);
    apply_theme(Pet::Dark);
    update_static_state(shared_view, false);
    animated_pose = pose_for(Pet::Offline, 0.0f);
    update_lines(animated_pose, StateColors[Pet::Offline]);
    lv_timer_create(animate_ui, 16, nullptr);
}

#ifndef PET_DESKTOP_PREVIEW
std::atomic<uint32_t> rendered_frames{0};
std::atomic<uint32_t> render_time_us{0};
int64_t render_started_us = 0;

void display_metrics(lv_event_t *event)
{
    if (lv_event_get_code(event) == LV_EVENT_REFR_START) {
        render_started_us = esp_timer_get_time();
    } else if (lv_event_get_code(event) == LV_EVENT_RENDER_READY) {
        render_time_us.fetch_add(static_cast<uint32_t>(esp_timer_get_time() - render_started_us),
                                 std::memory_order_relaxed);
        rendered_frames.fetch_add(1, std::memory_order_relaxed);
    }
}

void serial_task(void *)
{
    int64_t last_hello = 0;
    int64_t last_media_hello = 0;
    int64_t last_stat = esp_timer_get_time();
    int64_t last_byte[2]{};
    static Pet::Receiver bluetooth_receiver;
    int64_t last_yield = 0;
    stat_started_us = last_stat;
    static uint8_t buffer[16384];

    for (;;) {
        const int64_t now = esp_timer_get_time();
        ModelPicker::Command command;
        while (ModelPicker::take_command(command)) {
            DeviceLink::printf("@MODEL_PICK %s %lu %u %u\n", command.action,
                        static_cast<unsigned long>(command.context), command.request, command.key);
            std::fflush(stdout);
        }
        const int session_step = consume_session_step();
        if (session_step) {
            DeviceLink::printf("@NAV %d\n", session_step > 0 ? 1 : -1);
            std::fflush(stdout);
        }
        ModelPicker::button(board_theme_button_pressed());
        int count = 0;
        for (int transport = 0; transport < 2; ++transport) {
        const auto source = transport ? DeviceLink::Source::Bluetooth : DeviceLink::Source::Usb;
        auto &receiver = transport ? bluetooth_receiver : ::receiver;
        count = transport ? DeviceLink::read_bluetooth(buffer, sizeof(buffer)) :
            usb_serial_jtag_read_bytes(buffer, sizeof(buffer), pdMS_TO_TICKS(2));
        if (count < 0) { receiver.reset_partial(); continue; }
        if (count > 0) {
            last_byte[transport] = now;
            for (int i = 0; i < count; ++i) {
                if (receiver.feed(buffer[i])) {
                    if (!DeviceLink::accept(source, esp_timer_get_time())) continue;
                    if (receiver.is_art()) {
                        if (transport) continue;
                        CelestialDevice::receive(receiver.payload(), receiver.payload_size());
                        continue;
                    }
                    if (receiver.is_media()) {
                        if (transport) continue;
                        const SdMediaPlayer::UploadResult result =
                            SdMediaPlayer::upload(receiver.payload(),
                                                  receiver.payload_size());
                        if (result.ok) {
                            DeviceLink::printf("@MEDIA_ACK %u ok offset=%lu crc=%lu complete=%u\n",
                                        receiver.sequence(),
                                        static_cast<unsigned long>(result.offset),
                                        static_cast<unsigned long>(result.crc),
                                        result.complete ? 1 : 0);
                        } else {
                            DeviceLink::printf("@MEDIA_ACK %u error reason=%s\n",
                                        receiver.sequence(), result.reason);
                        }
                        std::fflush(stdout);
                        continue;
                    }
                    Pet::View view = get_view();
                    // Valid heartbeat packets reach here too; unchanged views
                    // must keep the connection alive without a full payload.
                    last_packet_us = esp_timer_get_time();
                    if (receiver.is_disconnect()) {
                        view.state = Pet::Offline;
                        view.total = view.index = view.pending = 0;
                        view.duration_seconds = 0;
                        view.input_tps_x10 = view.output_tps_x10 = 0;
                        set_view(view, false);
                        CelestialDevice::set_state(view.state);
                    } else if (receiver.is_model_picker()) {
                        ModelPicker::receive(receiver.payload(), receiver.payload_size());
                    } else if (receiver.is_model_test()) {
                        CelestialDevice::test_models(receiver.model_key() != 0);
                    } else if (receiver.is_model()) {
                        CelestialDevice::set_model(receiver.model_key());
                    } else if (receiver.is_rotation()) {
                        CelestialDevice::set_rotation_mode(receiver.rotation_mode());
                    } else if (receiver.is_astra_period()) {
                        CelestialDevice::set_astra_period(receiver.model_key(), receiver.astra_minimum());
                    } else if (receiver.is_view()) {
                        receiver.copy_view(view);
                        set_view(view, true);
                        CelestialDevice::set_state(view.state);
                    }
                    DeviceLink::printf("@ACK %u state=%u total=%u\n",
                                receiver.sequence(), view.state, view.total);
                    std::fflush(stdout);
                }
            }
        }
        if (receiver.partial() && now - last_byte[transport] > (transport ? 2000000 : 350000)) {
            receiver.reset_partial();
        }
        }
        if (now - last_hello >= 2000000) {
            last_hello = now;
            DeviceLink::printf("@PET_HELLO 2\n");
            DeviceLink::printf("@USAGE_HELLO 2\n");
            DeviceLink::printf("@MODEL_TEST_HELLO 1\n");
            DeviceLink::printf("@MODEL_PICK_HELLO 1\n");
            DeviceLink::printf("@ROTATE_HELLO 1\n");
            DeviceLink::hello();
            std::fflush(stdout);
        }
        if (SdMediaPlayer::storage_ready() && now - last_media_hello >= 2000000) {
            last_media_hello = now;
            DeviceLink::printf("@MEDIA_CHUNK %u\n", Pet::MediaChunkBytes);
            DeviceLink::printf("@MEDIA_HELLO 1\n");
            std::fflush(stdout);
        }
        CelestialDevice::poll(now);
        bool is_connected;
        Pet::View view = get_view(&is_connected);
        if (is_connected && now - last_packet_us > 5000000) {
            view.state = Pet::Offline;
            view.total = 0;
            view.index = 0;
            view.pending = 0;
            set_view(view, false);
            DeviceLink::printf("@CONNECTION_LOST age_ms=%lld\n",
                        static_cast<long long>((now - last_packet_us) / 1000));
            std::fflush(stdout);
        }
        if (now - last_stat >= 5000000) {
            TearFreeDisplay::report();
            float fps = frame_count * 1000000.0f / static_cast<float>(now - stat_started_us);
            const uint32_t completed = rendered_frames.exchange(0, std::memory_order_relaxed);
            const uint32_t render_us = render_time_us.exchange(0, std::memory_order_relaxed);
            const float draw_fps = completed * 1000000.0f / static_cast<float>(now - stat_started_us);
            const float draw_ms = completed ? render_us / (1000.0f * completed) : 0;
            const Pet::Theme stat_theme = active_theme(view.theme);
            DeviceLink::printf("@STAT fps=%.1f ok=%lu crc_bad=%lu state=%u total=%u pending=%u "
                        "balance=%lu today_requests=%lu today_quota=%lu "
                        "input_tps=%.1f output_tps=%.1f stale=%u heap=%lu psram=%lu "
                        "brightness=%d theme=%u draw_fps=%.1f draw_ms=%.1f "
                        "serial_stack_free=%lu api_source=%u week_x10=%u expiry=%s link=%s\n",
                        fps, static_cast<unsigned long>(receiver.good + bluetooth_receiver.good),
                        static_cast<unsigned long>(receiver.bad + bluetooth_receiver.bad), view.state, view.total,
                        view.pending, static_cast<unsigned long>(view.balance_quota),
                        static_cast<unsigned long>(view.today_request_count),
                        static_cast<unsigned long>(view.today_quota),
                        view.input_tps_x10 == UINT32_MAX ? -1.0 : view.input_tps_x10 / 10.0,
                        view.output_tps_x10 == UINT32_MAX ? -1.0 : view.output_tps_x10 / 10.0,
                        view.usage_stale,
                        static_cast<unsigned long>(esp_get_free_heap_size()),
                        static_cast<unsigned long>(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)),
                        brightness, static_cast<unsigned>(stat_theme), draw_fps, draw_ms,
                        static_cast<unsigned long>(uxTaskGetStackHighWaterMark(nullptr)),
                        view.api_source, view.week_remaining_x10,
                        view.membership_expires_at[0] ? view.membership_expires_at : "unset",
                        DeviceLink::name());
            std::fflush(stdout);
            frame_count = 0;
            stat_started_us = now;
            last_stat = now;
        }
        if (!count || esp_timer_get_time() - last_yield >= 2000) {
            vTaskDelay(1);
            last_yield = esp_timer_get_time();
        }
    }
}

#endif
} // namespace

#ifndef PET_DESKTOP_PREVIEW
extern "C" void app_main(void)
{
    std::setvbuf(stdout, nullptr, _IONBF, 0);
    shared_view.state = Pet::Offline;
    shared_view.theme = Pet::Dark;

    esp_chip_info_t chip_info;
    esp_chip_info(&chip_info);
    ESP_LOGI(Tag, "ESP32-P4 rev %d, %d cores, free PSRAM %lu bytes",
             chip_info.revision, chip_info.cores,
             static_cast<unsigned long>(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)));

    board_display_handles_t display;
    ESP_ERROR_CHECK(board_display_init(&display));
    ESP_ERROR_CHECK(board_theme_button_init());

    lvgl_port_cfg_t lvgl_config = ESP_LVGL_PORT_INIT_CONFIG();
    lvgl_config.task_stack = 12288;
    lvgl_config.task_priority = 5;
    lvgl_config.task_affinity = 1;
    lvgl_config.task_max_sleep_ms = 5;
    ESP_ERROR_CHECK(lvgl_port_init(&lvgl_config));

    ESP_ERROR_CHECK(lvgl_port_lock(0) ? ESP_OK : ESP_ERR_TIMEOUT);
    lv_display_t *lv_display = TearFreeDisplay::create(display.panel, NativeWidth, NativeHeight);
    ESP_ERROR_CHECK(lv_display ? ESP_OK : ESP_ERR_NO_MEM);
    lv_display_add_event_cb(lv_display, display_metrics, LV_EVENT_REFR_START, nullptr);
    lv_display_add_event_cb(lv_display, display_metrics, LV_EVENT_RENDER_READY, nullptr);

    const lvgl_port_touch_cfg_t touch_config = {
        .disp = lv_display,
        .handle = display.touch,
        .scale = {
            .x = 1.0f,
            .y = 1.0f,
        },
    };
    ESP_ERROR_CHECK(lvgl_port_add_touch(&touch_config) ? ESP_OK : ESP_ERR_NO_MEM);

    create_ui();
    set_brightness(DefaultBrightness);
    lvgl_port_unlock();

    usb_serial_jtag_driver_config_t usb_config = {
        .tx_buffer_size = 4096,
        .rx_buffer_size = 32768,
    };
    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&usb_config));
    usb_serial_jtag_vfs_use_driver();

    DeviceLink::printf("@PET_HELLO 2\n");
    DeviceLink::printf("@USAGE_HELLO 2\n");
    ESP_LOGI(Tag, "Chinese expressive UI ready: 800x480, touch gestures enabled");
    // Media queries calculate CRCs through FATFS; leave enough headroom for
    // the stdio/FATFS call chain in addition to the packet parser.
    ESP_ERROR_CHECK(xTaskCreate(serial_task, "pet_serial", 12288, nullptr, 4, nullptr) == pdPASS
                        ? ESP_OK
                        : ESP_ERR_NO_MEM);
    DeviceLink::init();
}
#endif
