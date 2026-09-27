#include "model_picker.h"
#include "model_button.h"
#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstring>

LV_FONT_DECLARE(font_menu_18);

namespace ModelPicker {
namespace {
enum Status { Context, Loading, Ready, Queued, Applied, Unavailable, Failed, Gone };
constexpr int OptionCount = 6;
constexpr int RowHeight = 48;
const char *Names[OptionCount] = {
    "GPT-6 Astra", "GPT-6 Sol", "GPT-6 Luna",
    "GPT-5.6 Sol", "GPT-5.6 Terra", "GPT-5.6 Luna"};
const uint32_t Colors[OptionCount] = {
    0xE9A2EE, 0xFFBD62, 0xAABFFA, 0xFFBD62, 0x6ADAC4, 0xAABFFA};
std::atomic<bool> pressed{false};
std::atomic<uint32_t> revision{0};
std::atomic<uint8_t> inbox[PayloadBytes]{};
uint32_t received_revision = 0;
uint8_t message[PayloadBytes]{};
uint8_t title_bits[224 * 24 / 8]{};
uint16_t title_pixels[224 * 24]{};
bool title_dirty = true;
ModelButton::Controller button_controller;
lv_obj_t *panel, *viewport, *list, *selection, *fill, *ring, *status_label, *title;
lv_obj_t *labels[OptionCount], *dots[OptionCount], *close_button;
bool opened = false, sending = false, hold_pinned = false;
int selected = 0, status = Context, mask = 0, current = 0;
uint32_t context = 0, pinned_context = 0, last_interaction = 0, last_tick = 0;
uint16_t request = 0;
uint32_t last_keepalive = 0, palette_signature = 0;
float panel_x = 800, list_y = 0, selection_y = 0;
bool link_ok = false;
Command commands[16]{};
std::atomic<unsigned> command_read{0}, command_write{0};

uint32_t read32(const uint8_t *p) {
    return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24;
}
bool send(const char *action, int key = 0) {
    const unsigned write = command_write.load(std::memory_order_relaxed);
    if (write - command_read.load(std::memory_order_acquire) >= 16) return false;
    commands[write % 16] = {action, pinned_context, request, static_cast<uint8_t>(key)};
    command_write.store(write + 1, std::memory_order_release);
    return true;
}
lv_obj_t *box(lv_obj_t *parent, int x, int y, int w, int h) {
    auto *obj = lv_obj_create(parent);
    lv_obj_remove_style_all(obj);
    lv_obj_set_pos(obj, x, y);
    lv_obj_set_size(obj, w, h);
    lv_obj_clear_flag(obj, LV_OBJ_FLAG_SCROLLABLE);
    return obj;
}
lv_obj_t *text(lv_obj_t *parent, const char *value, int x, int y, int width) {
    auto *obj = lv_label_create(parent);
    lv_obj_set_style_text_font(obj, &font_menu_18, 0);
    lv_obj_set_style_text_letter_space(obj, 0, 0);
    lv_label_set_long_mode(obj, LV_LABEL_LONG_DOT);
    lv_label_set_text(obj, value);
    lv_obj_set_pos(obj, x, y);
    lv_obj_set_width(obj, width);
    return obj;
}
void close() {
    if (opened) send("close");
    else if (hold_pinned) send("cancel");
    hold_pinned = false;
    opened = false;
}
void begin_hold() {
    pinned_context = context;
    if (++request == 0) ++request;
    hold_pinned = send("pin");
}
void open(uint32_t now) {
    opened = true;
    sending = false;
    if (!hold_pinned) {
        pinned_context = context;
        if (++request == 0) ++request;
    }
    hold_pinned = false;
    status = Loading;
    mask = 0;
    selected = current > 0 ? current - 1 : 0;
    list_y = 0;
    selection_y = selected * RowHeight + 2;
    last_interaction = now;
    lv_obj_remove_flag(panel, LV_OBJ_FLAG_HIDDEN);
    lv_obj_move_foreground(panel);
    send("open");
}
void next() {
    if (sending) return;
    for (int i = 1; i <= OptionCount; ++i) {
        const int candidate = (selected + i) % OptionCount;
        if (mask & (1 << candidate)) { selected = candidate; break; }
    }
}
void confirm() {
    if (!link_ok || sending || status != Ready || !(mask & (1 << selected))) return;
    if (!send("confirm", selected + 1)) { status = Failed; return; }
    sending = true;
    status = Loading;
    // The request is already handed off; keep only the slide-out animation.
    close();
}
const char *status_text() {
    if (!link_ok) return "未连接上位机";
    switch (status) {
    case Ready: return "当前对话";
    case Queued: return "执行结束后切换";
    case Applied: return "模型已切换";
    case Unavailable: return "需启用模型控制";
    case Failed: return "切换失败";
    case Gone: return "对话已关闭";
    default: return "正在连接";
    }
}
}

void init(lv_obj_t *screen) {
    panel = box(screen, 800, 0, 320, 480);
    lv_obj_set_style_bg_opa(panel, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(panel, 1, 0);
    lv_obj_set_style_border_side(panel, LV_BORDER_SIDE_LEFT, 0);
    lv_obj_add_flag(panel, LV_OBJ_FLAG_CLICKABLE);
    text(panel, "模型选择", 24, 28, 240);
    title = lv_canvas_create(panel);
    lv_obj_set_pos(title, 24, 68);
    lv_canvas_set_buffer(title, title_pixels, 224, 24, LV_COLOR_FORMAT_RGB565);
    close_button = lv_button_create(panel);
    lv_obj_set_pos(close_button, 268, 18);
    lv_obj_set_size(close_button, 36, 36);
    lv_obj_set_style_shadow_width(close_button, 0, 0);
    lv_obj_set_style_radius(close_button, 6, 0);
    auto *cross = lv_label_create(close_button);
    lv_label_set_text(cross, LV_SYMBOL_CLOSE);
    lv_obj_center(cross);
    lv_obj_add_event_cb(close_button, [](lv_event_t *) { close(); }, LV_EVENT_CLICKED, nullptr);
    viewport = box(panel, 16, 118, 288, OptionCount * RowHeight);
    list = box(viewport, 0, 0, 288, OptionCount * RowHeight);
    selection = box(list, 0, 2, 288, RowHeight - 4);
    lv_obj_set_style_radius(selection, 6, 0);
    lv_obj_set_style_bg_opa(selection, 24, 0);
    lv_obj_set_style_border_width(selection, 1, 0);
    fill = box(selection, 0, 0, 0, RowHeight - 4);
    lv_obj_set_style_bg_opa(fill, 60, 0);
    for (int i = 0; i < OptionCount; ++i) {
        dots[i] = box(list, 16, i * RowHeight + 20, 8, 8);
        lv_obj_set_style_radius(dots[i], LV_RADIUS_CIRCLE, 0);
        lv_obj_set_style_bg_color(dots[i], lv_color_hex(Colors[i]), 0);
        lv_obj_set_style_bg_opa(dots[i], 255, 0);
        labels[i] = text(list, Names[i], 40, i * RowHeight + 12, 238);
    }
    status_label = text(panel, "", 24, 432, 274);
    lv_obj_add_flag(panel, LV_OBJ_FLAG_HIDDEN);
    // Keep the hold indicator small and separated from the conversation title.
    ring = lv_arc_create(screen);
    lv_obj_set_size(ring, 18, 18);
    lv_obj_set_pos(ring, 135, 460);
    lv_arc_set_rotation(ring, 270);
    lv_arc_set_bg_angles(ring, 0, 360);
    lv_arc_set_range(ring, 0, 1000);
    lv_obj_remove_style(ring, nullptr, LV_PART_KNOB);
    lv_obj_clear_flag(ring, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_style_arc_width(ring, 2, LV_PART_MAIN);
    lv_obj_set_style_arc_width(ring, 2, LV_PART_INDICATOR);
    lv_obj_add_flag(ring, LV_OBJ_FLAG_HIDDEN);
}

void button(bool value) { pressed.store(value, std::memory_order_relaxed); }
bool take_command(Command &command) {
    const unsigned read = command_read.load(std::memory_order_relaxed);
    if (read == command_write.load(std::memory_order_acquire)) return false;
    command = commands[read % 16];
    command_read.store(read + 1, std::memory_order_release);
    return true;
}
bool visible() { return opened || panel_x < 799.5f; }
void receive(const uint8_t *payload, size_t size) {
    if (size != PayloadBytes || payload[0] != 7 || payload[1] > Gone ||
        payload[2] > 63 || payload[3] > OptionCount) return;
    revision.fetch_add(1);
    for (unsigned i = 0; i < PayloadBytes; ++i) inbox[i].store(payload[i]);
    revision.fetch_add(1);
}

bool tick(uint32_t now, bool connected, uint32_t bg, uint32_t fg, uint32_t muted,
          uint32_t accent) {
    link_ok = connected;
    const uint32_t rev = revision.load();
    if (!(rev & 1) && rev != received_revision) {
        uint8_t candidate[PayloadBytes];
        for (unsigned i = 0; i < PayloadBytes; ++i) candidate[i] = inbox[i].load();
        if (rev == revision.load()) {
            received_revision = rev;
            std::memcpy(message, candidate, PayloadBytes);
            if (!opened) {
                context = read32(message + 4);
                current = message[3];
                if (std::memcmp(title_bits, message + 58, sizeof(title_bits))) {
                    std::memcpy(title_bits, message + 58, sizeof(title_bits));
                    title_dirty = true;
                }
            } else if (read32(message + 4) == pinned_context &&
                       (message[8] | (message[9] << 8)) == request) {
                status = message[1]; mask = message[2]; current = message[3];
                if (std::memcmp(title_bits, message + 58, sizeof(title_bits))) {
                    std::memcpy(title_bits, message + 58, sizeof(title_bits));
                    title_dirty = true;
                }
                if (status == Ready && !(mask & (1 << selected))) next();
            }
        }
    }
    const auto event = button_controller.update(pressed.load(), now, opened);
    if (pressed.load()) last_interaction = now;
    if (event == ModelButton::Event::BeginHold) begin_hold();
    if (event == ModelButton::Event::CancelHold && hold_pinned) {
        send("cancel");
        hold_pinned = false;
    }
    bool theme = event == ModelButton::Event::Theme;
    if (theme && hold_pinned) {
        send("cancel");
        hold_pinned = false;
    }
    if (event == ModelButton::Event::Open) open(now);
    if (event == ModelButton::Event::Next) next();
    if (event == ModelButton::Event::Confirm) confirm();
    if (opened && now - last_interaction > 15000) close();
    if (opened && now - last_keepalive >= 2000) {
        send("open");
        last_keepalive = now;
    }
    if (!opened && panel_x > 799.5f && button_controller.progress() == 0) {
        lv_obj_add_flag(ring, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(panel, LV_OBJ_FLAG_HIDDEN);
        return theme;
    }
    const float dt = std::clamp((now - last_tick) * .001f, .001f, .05f);
    last_tick = now;
    const float ease = 1.f - std::exp(-dt * 18.f);
    panel_x += ((opened ? 480.f : 800.f) - panel_x) * ease;
    list_y += (0.f - list_y) * ease;
    selection_y += (selected * RowHeight + 2.f - selection_y) * ease;
    lv_obj_set_x(panel, std::lround(panel_x));
    lv_obj_set_y(list, std::lround(list_y));
    lv_obj_set_y(selection, std::lround(selection_y));
    if (!opened && panel_x > 799.5f) lv_obj_add_flag(panel, LV_OBJ_FLAG_HIDDEN);
    const auto progress = button_controller.progress();
    lv_obj_set_width(fill, sending ? 288 :
                         (opened && status == Ready ? 288 * progress / 1000 : 0));
    if (!opened && progress > 110) {
        lv_obj_remove_flag(ring, LV_OBJ_FLAG_HIDDEN);
        lv_obj_move_foreground(ring);
        lv_arc_set_value(ring, progress);
    } else lv_obj_add_flag(ring, LV_OBJ_FLAG_HIDDEN);
    const uint32_t signature = bg ^ fg ^ muted ^ accent;
    if (palette_signature != signature) {
    palette_signature = signature;
    title_dirty = true;
    lv_obj_set_style_arc_color(ring, lv_color_hex(muted), LV_PART_MAIN);
    lv_obj_set_style_arc_color(ring, lv_color_hex(accent), LV_PART_INDICATOR);
    lv_obj_set_style_bg_color(panel, lv_color_hex(bg), 0);
    lv_obj_set_style_border_color(panel, lv_color_hex(muted), 0);
    lv_obj_set_style_text_color(panel, lv_color_hex(fg), 0);
    lv_obj_set_style_bg_color(close_button, lv_color_hex(muted), 0);
    lv_obj_set_style_bg_opa(close_button, 35, 0);
    lv_obj_set_style_bg_color(selection, lv_color_hex(accent), 0);
    lv_obj_set_style_border_color(selection, lv_color_hex(accent), 0);
    lv_obj_set_style_bg_color(fill, lv_color_hex(accent), 0);
    }
    if (title_dirty) {
        const uint16_t background = lv_color_to_u16(lv_color_hex(bg));
        const uint16_t foreground = lv_color_to_u16(lv_color_hex(muted));
        for (unsigned i = 0; i < 224 * 24; ++i)
            title_pixels[i] = title_bits[i / 8] & (0x80 >> (i % 8)) ? foreground : background;
        lv_obj_invalidate(title);
        title_dirty = false;
    }
    for (int i = 0; i < OptionCount; ++i) {
        lv_obj_set_style_text_color(labels[i], lv_color_hex(i == selected ? fg : muted), 0);
        lv_obj_set_style_opa(labels[i], (mask & (1 << i)) ? 255 : 100, 0);
    }
    if (std::strcmp(lv_label_get_text(status_label), status_text()))
        lv_label_set_text(status_label, status_text());
    return theme;
}
}
