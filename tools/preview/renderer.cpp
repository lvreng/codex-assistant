#include <cstdlib>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

// Compile the production UI verbatim. Only ESP hardware/RTOS entry points are stubbed.
#include "../../main/main.cpp"
#include "src/misc/lv_text_private.h"

namespace {
std::vector<uint16_t> pixels(800 * 480);
std::vector<uint16_t> draw_buffer(800 * 100);
lv_indev_t *pointer;
lv_point_t pointer_position{};
lv_indev_state_t pointer_state = LV_INDEV_STATE_RELEASED;

void flush(lv_display_t *display, const lv_area_t *area, uint8_t *data)
{
    const auto *source = reinterpret_cast<uint16_t *>(data);
    for (int y = area->y1; y <= area->y2; ++y)
        for (int x = area->x1; x <= area->x2; ++x)
            pixels.at(y * 800 + x) = *source++;
    lv_display_flush_ready(display);
}

void advance(int frames = 1)
{
    for (int i = 0; i < frames; ++i) {
        preview_time_us += 16000;
        lv_tick_inc(16);
        lv_timer_handler();
        lv_refr_now(nullptr);
    }
}

Pet::View sample()
{
    Pet::View view;
    view.state = Pet::Thinking;
    view.total = 3; view.index = 0;
    view.duration_seconds = 156;
    view.balance_quota = 11755000;
    view.spent_quota = 4085000;
    view.today_quota = 820000;
    view.today_request_count = 48;
    view.today_prompt_tokens = 39281;
    view.today_completion_tokens = 5640;
    view.input_tps_x10 = 12800;
    view.output_tps_x10 = 420;
    view.usage_stale = 0;
    view.model_count = 4;
    const char *names[] = {"GPT-5", "GPT-5 mini", "GPT-4.1", "o4-mini"};
    for (unsigned i = 0; i < 4; ++i) {
        std::snprintf(view.models[i].name, sizeof(view.models[i].name), "%s", names[i]);
        view.models[i].quota = i == 0 ? 640000 : 170000 / i;
        view.models[i].prompt_tokens = 9000 / (i + 1);
        view.models[i].completion_tokens = 1300 / (i + 1);
        view.models[i].request_count = 24 / (i + 1);
    }
    return view;
}

void require(bool pass, const char *message)
{
    if (!pass) throw std::runtime_error(message);
}

void pointer_at(lv_point_t position, lv_indev_state_t state)
{
    pointer_position = position; pointer_state = state;
    lv_indev_read(pointer);
    advance(2);
}

void gesture(lv_point_t from, lv_point_t to, int hold = 0)
{
    pointer_at(from, LV_INDEV_STATE_PRESSED);
    for (int i = 0; i < hold; ++i) {
        advance();
        lv_indev_read(pointer);
    }
    pointer_at(to, LV_INDEV_STATE_PRESSED);
    pointer_at(to, LV_INDEV_STATE_RELEASED);
}

void valid_glyphs(const lv_font_t *font, const char *text)
{
    uint32_t at = 0;
    while (text[at]) {
        const auto codepoint = lv_text_encoded_next(text, &at);
        lv_font_glyph_dsc_t glyph{};
        require(lv_font_get_glyph_dsc(font, &glyph, codepoint, 0) && !glyph.is_placeholder,
                "state label has a missing glyph");
    }
}

void tests()
{
    auto v = sample();
    int checks = 0;
    // Cycling back is important: layout/style changes must not leak into old themes.
    for (int pass = 0; pass < 2; ++pass) {
        for (unsigned t = 0; t < Pet::ThemeCount; ++t) {
            v.theme = static_cast<Pet::Theme>(t);
            local_theme_override = false;
            set_view(v, true);
            advance(2);
            require(shown_theme == v.theme, "theme did not propagate");
            require(lv_obj_has_flag(ui.stage, LV_OBJ_FLAG_HIDDEN) == false, "stage hidden");
            require(lv_obj_has_flag(ui.character, LV_OBJ_FLAG_HIDDEN) ==
                    !Character::supports(v.theme), "wrong character visibility");
            for (unsigned i = 0; i < 4; ++i) {
                require(lv_obj_has_flag(ui.model_name[i], LV_OBJ_FLAG_HIDDEN) == (i >= 2),
                        "home must show only two models");
                require(std::strcmp(lv_label_get_text(ui.detail_model_name[i]),
                                    v.models[i].name) == 0, "detail model missing");
            }
            lv_area_t data, stage, state, duration, balance;
            lv_obj_get_coords(ui.data_panel, &data);
            lv_obj_get_coords(ui.stage, &stage);
            lv_obj_get_coords(ui.state_name, &state);
            lv_obj_get_coords(ui.session_duration, &duration);
            lv_obj_get_coords(ui.balance_value, &balance);
            lv_point_t tap{(data.x1 + data.x2) / 2, std::max(220, (data.y1 + data.y2) / 2)};
            require(hit_home_data(tap), "data hit target does not match layout");
            require(!hit_home_data({(stage.x1 + stage.x2) / 2, (stage.y1 + stage.y2) / 2}),
                    "character incorrectly opens data");
            require(balance.x1 >= 0 && balance.x2 < 800 && balance.y2 < 480,
                    "balance exceeds screen");
            if (Character::supports(v.theme))
                require(state.x2 < duration.x1, "state overlaps duration");
            require(current_title_buffer[0] == lv_color_to_u16(color(theme_palette(v.theme).bg)),
                    "title retains old theme background");
            for (unsigned s = 0; s < Pet::StateCount; ++s) {
                v.state = static_cast<Pet::State>(s);
                set_view(v, true);
                advance(4);
                require(shown_state == v.state, "state did not propagate");
                const char *expected = v.theme == Pet::VanGogh
                    ? (v.state == Pet::Done ? "已完成" : "思考中")
                    : StateNames[s];
                require(std::strcmp(lv_label_get_text(ui.state_name), expected) == 0,
                        "wrong state text");
                valid_glyphs(lv_obj_get_style_text_font(ui.state_name, 0), expected);
                if (v.theme == Pet::VanGogh) {
                    require(lv_obj_has_flag(ui.face, LV_OBJ_FLAG_HIDDEN),
                            "painted theme still shows a face");
                    require(lv_obj_has_flag(ui.particles[0], LV_OBJ_FLAG_HIDDEN),
                            "painted theme still shows expression particles");
                    require(!lv_obj_has_flag(ui.vangogh_moon, LV_OBJ_FLAG_HIDDEN),
                            "painted moon missing");
                }
                if (Character::supports(v.theme)) {
                    lv_obj_get_coords(ui.state_name, &state);
                    lv_obj_get_coords(ui.session_duration, &duration);
                    require(state.x2 < duration.x1, "long state overlaps duration");
                }
            }
            gesture(tap, tap);
            advance(45);
            require(detail_target && detail_progress == 1, "tap did not open details");
            require(lv_obj_get_x(ui.detail_page) == 0, "details did not settle");
            gesture({30, 30}, {30, 30});
            advance(45);
            require(!detail_target && detail_progress == 0, "back did not close details");
            require(lv_obj_has_flag(ui.detail_page, LV_OBJ_FLAG_HIDDEN),
                    "closed details still intercept input");
            const auto old_state = shown_state;
            gesture({400, 200}, {270, 200});
            require(consume_session_step() == 1 && shown_state == old_state,
                    "left swipe must request next session, not change expression");
            gesture({300, 200}, {430, 200});
            require(consume_session_step() == -1, "right swipe must request previous session");
            gesture({400, 200}, {400, 200}, 40);
            require(shown_theme == v.theme, "long press unexpectedly changed theme");

            // Interrupt an in-flight title transition by pressing the hardware button.
            v.labels[8] ^= 0x08;
            set_view(v, true);
            advance(2);
            require(title_transition_active, "title update did not animate");
            request_theme_cycle();
            advance();
            require(shown_theme == static_cast<Pet::Theme>((t + 1) % Pet::ThemeCount),
                    "physical button cycle failed");
            require(!title_transition_active && lv_obj_get_width(ui.next_session_title) == 150,
                    "title transition left stale dimensions on theme switch");
            require((std::strncmp(lv_label_get_text(ui.session_duration), "TIME ", 5) != 0) ==
                    Character::supports(shown_theme), "theme switch retained old duration format");
            ++checks;
        }
    }
    local_theme_override = false;
    v.theme = Pet::VanGogh;
    v.state = Pet::Done;
    set_view(v, true); advance(180);
    require(std::abs(lv_obj_get_x(ui.vangogh_moon) - 426) < 2,
            "completed moon did not return to the painted position");
    v.state = Pet::Thinking;
    set_view(v, true); advance(180);
    require(std::abs(lv_obj_get_x(ui.vangogh_moon) - 213) < 3,
            "thinking moon did not follow the arc to the center");
    const auto moving_x = lv_obj_get_x(ui.vangogh_moon);
    v.state = Pet::Done;
    set_view(v, true); advance(3);
    require(lv_obj_get_x(ui.vangogh_moon) > moving_x &&
            lv_obj_get_x(ui.vangogh_moon) < 426,
            "interrupted moon motion jumped to the end");
    advance(180);
    require(std::abs(lv_obj_get_x(ui.vangogh_moon) - 426) < 2,
            "moon failed to return after interruption");
    v.theme = Pet::Glass;
    v.input_tps_x10 = 3210; v.output_tps_x10 = 890;
    set_view(v, true);
    advance(70);
    require(std::strstr(lv_label_get_text(ui.input_rate), "321.0") != nullptr,
            "input rate did not update");
    const auto *input = lv_chart_get_y_array(ui.detail_chart, ui.input_series);
    require(std::find(input, input + lv_chart_get_point_count(ui.detail_chart), 3210) !=
            input + lv_chart_get_point_count(ui.detail_chart), "chart did not record live rate");
    v.balance_quota = UINT32_MAX; v.model_count = 0;
    v.input_tps_x10 = v.output_tps_x10 = UINT32_MAX;
    set_view(v, false); advance(3);
    require(std::strcmp(lv_label_get_text(ui.balance_value), "--") == 0,
            "missing balance retained old value");
    require(std::strcmp(lv_label_get_text(ui.model_name[0]), "-") == 0,
            "empty model list retained old data");
    for (unsigned t = 0; t < Pet::ThemeCount; ++t) {
        v.theme = static_cast<Pet::Theme>(t);
        v.api_source = 1; v.account_kind = 1; v.week_remaining_x10 = 980;
        std::snprintf(v.plan_name, sizeof(v.plan_name), "Pro Lite");
        std::snprintf(v.week_reset, sizeof(v.week_reset), "09-29 11:29");
        set_view(v, true); advance(40);
        require(lv_arc_get_value(ui.quota_ring) == 980, "weekly quota ring did not settle");
        require(lv_obj_has_flag(ui.balance_value, LV_OBJ_FLAG_HIDDEN), "official source shows balance");
        for (lv_obj_t *obj : {ui.api_caption, ui.quota_percent, ui.account_plan,
                              ui.account_expiry, ui.account_reset}) {
            valid_glyphs(lv_obj_get_style_text_font(obj, 0), lv_label_get_text(obj));
            lv_area_t box;
            lv_obj_get_coords(obj, &box);
            require(box.x1 >= 0 && box.y1 >= 0 && box.x2 < 800 && box.y2 < 480,
                    "account label exceeds display");
        }
        lv_area_t ring, expiry, reset, input_rate;
        lv_obj_get_coords(ui.quota_ring, &ring);
        lv_obj_get_coords(ui.account_expiry, &expiry);
        lv_obj_get_coords(ui.account_reset, &reset);
        lv_obj_get_coords(ui.input_rate, &input_rate);
        if (v.theme != Pet::Paper) {
            require(ring.y2 < expiry.y1 && expiry.y2 < reset.y1 && reset.y2 < input_rate.y1,
                    "account rows overlap");
        }
        v.api_source = 2;
        set_view(v, true); advance(2);
        require(lv_obj_has_flag(ui.quota_ring, LV_OBJ_FLAG_HIDDEN), "MoreCode retained official quota");
        require(!lv_obj_has_flag(ui.balance_value, LV_OBJ_FLAG_HIDDEN), "MoreCode balance not restored");
    }
    std::cout << "UI contracts: " << checks << " theme cycles, 126 states; "
                 "glyphs, touch, title interruption, live rates and empty data passed\n";
}

void save(const std::string &path)
{
    std::ofstream out(path, std::ios::binary);
    if (!out) throw std::runtime_error("cannot open output");
    out << "P6\n800 480\n255\n";
    for (uint16_t pixel : pixels) {
        const unsigned char rgb[] = {
            static_cast<unsigned char>(((pixel >> 11) & 31) * 255 / 31),
            static_cast<unsigned char>(((pixel >> 5) & 63) * 255 / 63),
            static_cast<unsigned char>((pixel & 31) * 255 / 31)};
        out.write(reinterpret_cast<const char *>(rgb), 3);
    }
}

void picker_preview(const std::string &prefix)
{
    auto view = sample();
    view.theme = Pet::Adaptive;
    set_view(view, true);
    advance(5);
    uint8_t context[ModelPicker::PayloadBytes] = {7, 0, 0, 1, 23, 0, 0, 0, 0, 0};
    std::memcpy(context + 10, "Codex", 6);
    ModelPicker::receive(context, sizeof(context));
    advance(3);
    lv_obj_t *hold_ring = nullptr;
    for (uint32_t i = 0; i < lv_obj_get_child_count(lv_screen_active()); ++i) {
        auto *child = lv_obj_get_child(lv_screen_active(), i);
        lv_area_t area;
        lv_obj_get_coords(child, &area);
        if (lv_obj_check_type(child, &lv_arc_class) && area.x1 == 135 &&
            area.y1 == 460 && area.x2 == 152 && area.y2 == 477)
            hold_ring = child;
    }
    require(hold_ring, "model picker hold ring geometry missing");
    require(lv_obj_get_style_arc_width(hold_ring, LV_PART_MAIN) == 2 &&
            lv_obj_get_style_arc_width(hold_ring, LV_PART_INDICATOR) == 2,
            "model picker hold ring width changed");
    const auto initial_theme = shown_theme;
    ModelPicker::button(true);
    advance(34);
    require(!ModelPicker::visible(), "sidebar opened before hold threshold");
    save(prefix + "-ring.ppm");
    advance(30);
    require(ModelPicker::visible(), "sidebar did not open on long hold");
    context[1] = 2; context[2] = 63; context[8] = 1;
    ModelPicker::receive(context, sizeof(context));
    ModelPicker::button(false);
    advance(32);
    save(prefix + "-open.ppm");
    for (int i = 0; i < 5; ++i) {
        ModelPicker::button(true); advance(5);
        ModelPicker::button(false); advance(5);
    }
    advance(25);
    save(prefix + "-last-model.ppm");
    ModelPicker::button(true); advance(60);
    save(prefix + "-confirm.ppm");
    advance(30);
    require(!ModelPicker::visible(), "confirm did not close within 0.5 seconds");
    ModelPicker::button(false); advance(4);
    require(shown_theme == initial_theme, "confirm hold release changed theme");
    context[1] = 3;
    ModelPicker::receive(context, sizeof(context));
    advance(15);
    save(prefix + "-queued.ppm");
    require(!ModelPicker::visible(), "queued status reopened picker");
    context[1] = 4; context[3] = 6;
    ModelPicker::receive(context, sizeof(context));
    advance(15);
    save(prefix + "-applied.ppm");
    require(!ModelPicker::visible(), "applied status reopened picker");
    gesture({766, 36}, {766, 36});
    advance(40);
    require(!ModelPicker::visible(), "sidebar close failed");
    ModelPicker::Command command;
    bool pinned = false, confirmed = false, closed = false;
    while (ModelPicker::take_command(command)) {
        require(command.context == 23 && command.request == 1,
                "picker command changed conversation context");
        if (std::strcmp(command.action, "pin") == 0) {
            require(!pinned && !confirmed, "conversation was not pinned before confirmation");
            pinned = true;
        } else if (std::strcmp(command.action, "confirm") == 0) {
            require(pinned, "confirmation arrived before conversation pin");
            require(!confirmed && command.key == 6, "confirmation repeated or chose wrong model");
            confirmed = true;
        } else if (std::strcmp(command.action, "close") == 0) {
            require(confirmed, "sidebar closed before submitting selection");
            closed = true;
        }
    }
    require(pinned && confirmed && closed, "pin, selection and close commands not delivered in order");
    for (const auto *label : {"模型选择", "未连接上位机", "执行结束后切换", "模型已切换",
                               "需启用模型控制", "切换失败", "对话已关闭", "正在连接"})
        valid_glyphs(&font_menu_18, label);
    for (const auto *label : {"GPT-6 Astra", "GPT-6 Sol", "GPT-6 Luna",
                               "GPT-5.6 Sol", "GPT-5.6 Terra", "GPT-5.6 Luna"})
        valid_glyphs(&font_menu_18, label);
    for (const auto *label : {"对话滚动", "关闭滚动", "只滚动执行任务", "滚动全部"})
        valid_glyphs(&font_menu_18, label);
    std::cout << "Picker preview: six framebuffer snapshots and hold/close checks passed\n";
}
} // namespace

int main(int argc, char **argv)
{
    try {
        lv_init();
        auto *display = lv_display_create(800, 480);
        lv_display_set_color_format(display, LV_COLOR_FORMAT_RGB565);
        lv_display_set_buffers(display, draw_buffer.data(), nullptr,
                               draw_buffer.size() * 2, LV_DISPLAY_RENDER_MODE_PARTIAL);
        lv_display_set_flush_cb(display, flush);
        create_ui();
        pointer = lv_indev_create();
        lv_indev_set_type(pointer, LV_INDEV_TYPE_POINTER);
        lv_indev_set_mode(pointer, LV_INDEV_MODE_EVENT);
        lv_indev_set_display(pointer, display);
        lv_indev_set_read_cb(pointer, [](lv_indev_t *, lv_indev_data_t *data) {
            data->point = pointer_position; data->state = pointer_state;
        });
        if (argc == 2 && std::string(argv[1]) == "--test") {
            tests();
            return 0;
        }
        if (argc == 3 && std::string(argv[1]) == "--picker") {
            picker_preview(argv[2]);
            return 0;
        }
        if (argc < 6) {
            std::cerr << "pet_preview THEME STATE SECONDS LABELS OUTPUT "
                         "[FRAMES] [PREVIOUS_STATE] [details]\n";
            return 1;
        }
        auto v = sample();
        bool found = false;
        for (unsigned t = 0; t < Pet::ThemeCount; ++t)
            if (std::string(argv[1]) == theme_name(static_cast<Pet::Theme>(t))) {
                v.theme = static_cast<Pet::Theme>(t); found = true;
            }
        require(found, "unknown theme");
        const int state = std::stoi(argv[2]);
        require(state >= 0 && state < Pet::StateCount, "unknown state");
        v.state = static_cast<Pet::State>(state);
        std::ifstream labels_file(argv[4], std::ios::binary);
        labels_file.read(reinterpret_cast<char *>(v.labels), Pet::LabelBytes);
        require(labels_file.gcount() == Pet::LabelBytes, "invalid label data");
        if (argc > 7) {
            const auto requested = v.state;
            const int previous = std::stoi(argv[7]);
            require(previous >= 0 && previous < Pet::StateCount, "invalid previous state");
            v.state = static_cast<Pet::State>(previous);
            set_view(v, true); advance(90);
            v.state = requested;
        }
        set_view(v, true);
        const float seconds = std::stof(argv[3]);
        require(seconds >= 0 && seconds <= 60, "invalid preview time");
        advance(std::max(1, static_cast<int>(seconds / .016f)));
        if (argc > 8 && std::string(argv[8]) == "details") {
            detail_target = true;
            lv_obj_remove_flag(ui.detail_page, LV_OBJ_FLAG_HIDDEN);
            advance(50);
        }
        const int frames = argc > 6 ? std::stoi(argv[6]) : 1;
        require(frames >= 1 && frames <= 2000, "invalid frame count");
        for (int i = 0; i < frames; ++i) {
            const auto path = frames == 1 ? std::string(argv[5]) :
                std::string(argv[5]) + "-" + std::to_string(i) + ".ppm";
            save(path);
            advance(3);
            if (frames > 100 && (i + 1) % 100 == 0)
                std::cerr << "Frames " << i + 1 << "/" << frames << '\n';
        }
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << "\n";
        return 1;
    }
}
