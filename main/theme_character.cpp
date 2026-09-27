#include "theme_character.h"
#include "character_motion.h"
#include "character_cache.h"
#include "generated/character_assets.h"

#include <algorithm>
#include <cmath>

namespace Character {
namespace {
constexpr float Pi = 3.14159265359f;
struct Rig {
    lv_obj_t *object = nullptr;
    Pet::Theme theme = Pet::Mechanical;
    Motion motion;
} rig;

struct CharacterStyle {
    uint32_t eye_shadow;
    uint32_t lid_edge;
    uint32_t happy_line;
};

const CharacterStyle &character_style()
{
    static constexpr CharacterStyle mechanical{
        0xCBD5DB, 0x65757F, 0x18232B,
    };
    static constexpr CharacterStyle paper{
        0xF2E1D8, 0xD75244, 0x194A94,
    };
    static constexpr CharacterStyle glass{
        0xC9DFDD, 0x037DA7, 0x122E39,
    };
    switch (rig.theme) {
    case Pet::Paper:
        return paper;
    case Pet::Glass:
        return glass;
    default:
        return mechanical;
    }
}

struct Paint {
    lv_layer_t *layer;
    float ox, oy, scale;
    float dx = 0, dy = 0, roll = 0, stretch = 1;

    lv_point_precise_t point(float x, float y) const
    {
        const float cx = x - 240, cy = (y - 160) * stretch;
        const float c = std::cos(roll), s = std::sin(roll);
        return {static_cast<lv_value_precise_t>(std::lround(
                    ox + (240 + cx * c - cy * s + dx) * scale)),
                static_cast<lv_value_precise_t>(std::lround(
                    oy + (160 + cx * s + cy * c + dy) * scale))};
    }

    void sprite(const lv_image_dsc_t &image, float x, float y, float w, float h,
                float angle = 0, lv_opa_t opacity = LV_OPA_COVER) const
    {
        if (!opacity) return;
        const auto center = point(x, y);
        lv_draw_image_dsc_t d;
        lv_draw_image_dsc_init(&d);
        if (const auto *cached = material_pose(image, w * scale, h * scale, roll + angle)) {
            d.src = cached;
            d.opa = opacity;
            const int32_t left = static_cast<int32_t>(center.x) - cached->header.w / 2;
            const int32_t top = static_cast<int32_t>(center.y) - cached->header.h / 2;
            const lv_area_t area{left, top, left + static_cast<int32_t>(cached->header.w) - 1,
                                 top + static_cast<int32_t>(cached->header.h) - 1};
            lv_draw_image(layer, &d, &area);
            return;
        }
        d.src = &image;
        d.pivot.x = image.header.w / 2;
        d.pivot.y = image.header.h / 2;
        d.scale_x = std::lround(w * scale * 256 / image.header.w);
        d.scale_y = std::lround(h * scale * stretch * 256 / image.header.h);
        d.rotation = std::lround((roll + angle) * 1800 / Pi);
        d.antialias = 1;
        d.opa = opacity;
        const int32_t left = static_cast<int32_t>(center.x) - d.pivot.x;
        const int32_t top = static_cast<int32_t>(center.y) - d.pivot.y;
        const lv_area_t area{left, top, left + static_cast<int32_t>(image.header.w) - 1,
                             top + static_cast<int32_t>(image.header.h) - 1};
        lv_draw_image(layer, &d, &area);
    }

    void disc(float x, float y, float w, float h, uint32_t rgb,
              lv_opa_t opacity = LV_OPA_COVER) const
    {
        const auto center = point(x, y);
        const int width = std::max(1, static_cast<int>(std::lround(w * scale)));
        const int height = std::max(1, static_cast<int>(std::lround(h * scale * stretch)));
        const int32_t left = static_cast<int32_t>(center.x) - width / 2;
        const int32_t top = static_cast<int32_t>(center.y) - height / 2;
        const lv_area_t area{left, top, left + width - 1, top + height - 1};
        lv_draw_rect_dsc_t d;
        lv_draw_rect_dsc_init(&d);
        d.bg_color = lv_color_hex(rgb);
        d.bg_opa = opacity;
        d.radius = LV_RADIUS_CIRCLE;
        lv_draw_rect(layer, &d, &area);
    }

    void line(float x1, float y1, float x2, float y2, float width, uint32_t rgb,
              lv_opa_t opacity = LV_OPA_COVER) const
    {
        lv_draw_line_dsc_t d;
        lv_draw_line_dsc_init(&d);
        d.p1 = point(x1, y1); d.p2 = point(x2, y2);
        d.color = lv_color_hex(rgb); d.opa = opacity;
        d.width = std::max(1, static_cast<int>(std::lround(width * scale)));
        d.round_start = d.round_end = true;
        lv_draw_line(layer, &d);
    }

    void tri(float ax, float ay, float bx, float by, float cx, float cy,
             uint32_t rgb) const
    {
        lv_draw_triangle_dsc_t d;
        lv_draw_triangle_dsc_init(&d);
        d.p[0] = point(ax, ay); d.p[1] = point(bx, by); d.p[2] = point(cx, cy);
        d.bg_color = lv_color_hex(rgb);
        lv_draw_triangle(layer, &d);
    }

    void quad(float ax, float ay, float bx, float by, float cx, float cy,
              float dx_, float dy_, uint32_t rgb) const
    {
        tri(ax, ay, bx, by, cx, cy, rgb);
        tri(ax, ay, cx, cy, dx_, dy_, rgb);
    }

    void curve(float x, float y, float width, float depth, float weight,
               uint32_t rgb, lv_opa_t opacity = LV_OPA_COVER) const
    {
        float px = x - width / 2, py = y;
        for (int i = 1; i <= 20; ++i) {
            const float u = i / 20.0f;
            const float nx = x - width / 2 + u * width;
            const float ny = y + 4 * depth * u * (1 - u);
            line(px, py, nx, ny, weight, rgb, opacity);
            px = nx; py = ny;
        }
    }
};

void eyelid(const Paint &p, float x, float y, float radius, float opening,
            const lv_image_dsc_t &material, uint32_t edge, lv_opa_t edge_opacity)
{
    opening = std::clamp(opening, 0.0f, 1.0f);
    if (opening >= .995f) return;
    if (opening <= .16f) opening = 0;

    if (const auto *pose = eyelid_pose(material, opening, &opening)) {
        p.sprite(*pose, x, y, radius * 2, radius * 2);
    } else {
        const float gap = radius * opening;
        const auto center = p.point(x, y);
        const int32_t gap_px = std::lround(gap * p.scale * p.stretch);
        const int32_t upper_edge = static_cast<int32_t>(center.y) - gap_px;
        const int32_t lower_edge = static_cast<int32_t>(center.y) + gap_px;
        const lv_area_t original = p.layer->_clip_area;

        p.layer->_clip_area.y2 = std::min(original.y2, upper_edge);
        if (p.layer->_clip_area.y2 >= original.y1)
            p.sprite(material, x, y, radius * 2, radius * 2);

        p.layer->_clip_area = original;
        p.layer->_clip_area.y1 = std::max(original.y1, lower_edge);
        if (p.layer->_clip_area.y1 <= original.y2)
            p.sprite(material, x, y, radius * 2, radius * 2);
        p.layer->_clip_area = original;
    }

    const float gap = radius * opening;
    const float reach = std::sqrt(std::max(0.0f, radius * radius - gap * gap));

    if (!edge_opacity) return;
    if (opening == 0) {
        p.curve(x, y - 1, radius * 1.55f, 3.2f, 1.55f, edge, edge_opacity);
        return;
    }
    const float edge_curve = std::min(2.2f, 5.0f * (1 - opening));
    p.curve(x, y - gap, reach * 2 - 2, edge_curve, 1.3f, edge, edge_opacity);
    p.curve(x, y + gap, reach * 2 - 2, -edge_curve, 1.15f, edge,
            static_cast<lv_opa_t>(edge_opacity * .72f));
}

void eye(const Paint &p, float x, float y, float radius, float opening, const Pose &s)
{
    const bool metal = rig.theme == Pet::Mechanical;
    const bool paper = rig.theme == Pet::Paper;
    const CharacterStyle &style = character_style();
    const float happy = std::clamp(s[Happy], 0.0f, 1.0f);
    const float lid_opening =
        std::min(1.0f, opening) * std::max(.01f, 1 - happy * 1.2f);
    if (metal) {
        p.disc(x, y + 1, radius * 2 + 13, radius * 2 + 13, 0x7C898F);
        p.disc(x, y - 1, radius * 2 + 10, radius * 2 + 10, 0xF9FCFE);
        p.disc(x, y + 1, radius * 2 + 6, radius * 2 + 6, 0x707D84);
    } else {
        p.disc(x + .5f, y + 2, radius * 2 + 3, radius * 2 + 3,
               style.eye_shadow, 120);
    }
    p.sprite(paper ? Assets::paper_eye : Assets::eye_white,
             x, y, radius * 2, radius * 2);
    const float pr = radius * (paper ? .49f : .66f) * s[Pupil];
    float gx = s[GazeX], gy = s[GazeY];
    const float reach = std::max(0.0f, radius - pr - 2);
    const float length = std::sqrt(gx * gx + gy * gy);
    if (length > reach) { gx *= reach / length; gy *= reach / length; }
    const auto &iris = metal ? Assets::metal_iris : paper ? Assets::paper_iris : Assets::glass_iris;
    const float reveal_progress =
        std::clamp((lid_opening - .16f) / .22f, 0.0f, 1.0f);
    const float iris_reveal =
        reveal_progress * reveal_progress * (3 - 2 * reveal_progress);
    p.sprite(iris, x + gx, y + gy, pr * 2, pr * 2, 0,
             static_cast<lv_opa_t>(
                 255 * (1 - happy) * (1 - happy) * iris_reveal));
    const auto &lid = metal ? Assets::metal_lid : paper ? Assets::paper_lid : Assets::glass_lid;
    eyelid(p, x, y, radius, lid_opening, lid, style.lid_edge,
            static_cast<lv_opa_t>(255 * std::max(0.0f, 1 - happy * 2)));
    if (happy > .03f)
        p.curve(x, y + 14, radius * 1.3f, -14, paper ? 3 : 4.2f,
                style.happy_line, static_cast<lv_opa_t>(255 * happy));
}

void mouth(const Paint &p, float x, float y, const Pose &s, uint32_t rgb, float size = 1)
{
    x += s[MouthX] * size;
    const float width = s[MouthWidth] * size, depth = s[Smile] * size;
    const float jaw = s[Jaw] * size;
    const float weight = (rig.theme == Pet::Paper ? 3.4f : 4.6f) * size;
    const float open = std::clamp(jaw / 7.0f, 0.0f, 1.0f);
    if (jaw > 1) {
        p.disc(x, y + jaw * .45f, width, jaw + 4, rgb, static_cast<lv_opa_t>(255 * open));
        if (s[Happy] > .1f)
            p.disc(x, y + jaw * .65f, width * .6f, jaw * .31f, 0xFF9981,
                   static_cast<lv_opa_t>(255 * open));
    }
    if (s[Alarm] > .01f) {
        float px = x - width / 2, py = y;
        for (int i = 1; i <= 12; ++i) {
            const float u = i / 12.0f;
            const float nx = x - width / 2 + u * width;
            const float zig = 4 - 11 * (1 - std::abs(std::fmod(u * 4, 2.0f) - 1));
            const float ny = y + 4 * depth * u * (1 - u) * (1 - s[Alarm]) +
                             zig * s[Alarm] * size;
            p.line(px, py, nx, ny, weight, rgb);
            px = nx; py = ny;
        }
    } else if (open < 1) {
        p.curve(x, y + 1.4f, width, depth, weight + .8f, 0xFFFFFF,
                static_cast<lv_opa_t>(55 * (1 - open)));
        p.curve(x, y, width, depth, weight, rgb, static_cast<lv_opa_t>(255 * (1 - open)));
    }
}

void accents(const Paint &p, const Pose &s, float radius, uint32_t rgb)
{
    if (s[Celebrate] < .05f) return;
    const float amount = s[Celebrate];
    for (int side : {-1, 1})
        for (int i = 0; i < 3; ++i) {
            const float x = 240 + side * (radius + 4 * amount), y = 69 + i * 18;
            p.line(x, y, x + side * (7 + 6 * amount), y - 8 + i * 5,
                   2.5f, rgb, static_cast<lv_opa_t>(220 * amount));
        }
}

void mechanical(const Paint &p, const Pose &s)
{
    p.sprite(Assets::metal_shell, 240, 158, 388, 282);
    for (int side = 0; side < 2; ++side) {
        const float x = side ? 323 : 157;
        eye(p, x, 145, 49, s[side ? EyeR : EyeL], s);
        p.sprite(Assets::metal_brow, x, 78 + s[BrowLift], 114, 33,
                 s[side ? BrowR : BrowL] * Pi / 180);
    }
    mouth(p, 240, 242, s, 0x25343D);
    accents(p, s, 196, 0xDC5541);
}

void hand(const Paint &p, float sx, float sy, float hx, float hy, bool right, float spread)
{
    const float ex = (sx + hx) / 2 + (right ? 14 : -14), ey = (sy + hy) / 2 + 17;
    p.tri(sx - 7, sy, sx + 7, sy, ex, ey + 10, 0xCE423B);
    p.tri(sx + 7, sy, ex + 9, ey, ex, ey + 10, 0xFF8A69);
    p.quad(ex - 8, ey, ex + 7, ey + 8, hx + 8, hy + 8, hx - 8, hy - 6, 0xEE6652);
    p.line(ex - 7, ey + 1, hx - 7, hy - 4, 1.3f, 0xF9B693);
    p.quad(hx - 10, hy - 6, hx + 9, hy - 9, hx + 12, hy + 9,
           hx - 6, hy + 12, 0xFF735C);
    p.tri(hx - 10, hy - 6, hx + 12, hy + 9, hx - 6, hy + 12, 0xE65B4B);
    for (int i = 0; i < 4; ++i) {
        const float angle = (-2.7f + i * .56f) + (right ? .22f : -.22f);
        p.line(hx - 6 + i * 5, hy - 4,
               hx + std::cos(angle) * (13 + spread * 7),
               hy + std::sin(angle) * (14 + spread * 8), 3.5f, 0xF96B56);
    }
}

void paper(const Paint &p, const Pose &s)
{
    Paint body = p;
    body.roll = s[BodyRoll]; body.dy += s[BodyY];
    body.sprite(Assets::paper_body, 240, 247, 113, 125);
    p.sprite(Assets::paper_head, 240, 116, 242, 193);
    for (int side = 0; side < 2; ++side) {
        const float x = side ? 291 : 190;
        eye(p, x, 127, 39, s[side ? EyeR : EyeL], s);
        const float by = 77 + s[BrowLift], tilt = s[side ? BrowR : BrowL] * .6f;
        p.tri(x - 31, by - tilt, x + 29, by + tilt, x - 15, by + 10, 0xD74D40);
        p.tri(x - 31, by - tilt, x + 29, by + tilt, x + 19, by - 8, 0xFFA285);
        p.line(x - 29, by - tilt, x + 28, by + tilt, 1, 0xFFC0A4);
    }
    mouth(p, 241, 179, s, 0x963B39, .68f);
    const float spread = std::max(s[Happy], s[Alarm]);
    hand(body, 202, 207, s[HandLX], s[HandLY], false, spread);
    hand(body, 278, 207, s[HandRX], s[HandRY], true, spread);
    accents(p, s, 133, 0xD9AB22);
}

void glass(const Paint &p, const Pose &s)
{
    p.sprite(Assets::glass_shell, 239, 159, 388, 277);
    for (int side = 0; side < 2; ++side) {
        const float x = side ? 298 : 179;
        eye(p, x, 154, 44, s[side ? EyeR : EyeL], s);
        p.sprite(Assets::glass_brow, x, 98 + s[BrowLift], 84, 25,
                 s[side ? BrowR : BrowL] * Pi / 180);
    }
    mouth(p, 239, 226, s, 0xDF4545);
    accents(p, s, 194, 0xA6C839);
}

void draw(lv_event_t *event)
{
    if (!supports(rig.theme)) return;
    lv_area_t a;
    lv_obj_get_coords(rig.object, &a);
    const float scale = std::min(lv_area_get_width(&a) / 480.0f,
                                 lv_area_get_height(&a) / 320.0f);
    Paint p{lv_event_get_layer(event),
            a.x1 + (lv_area_get_width(&a) - 480 * scale) / 2,
            a.y1 + (lv_area_get_height(&a) - 320 * scale) / 2, scale};
    Pose shown = rig.motion.pose();
    const bool paper_theme = rig.theme == Pet::Paper;
    p.sprite(Assets::shadow, 240, paper_theme ? 309 : 303,
             paper_theme ? 170 : 368, 29, 0,
             static_cast<lv_opa_t>(std::clamp(230 + shown[Y] * 3, 160.0f, 255.0f)));
    p.dx = shown[X]; p.dy = shown[Y];
    p.roll = shown[Roll];
    // Rigid materials keep their dimensions; anticipation moves the whole head.
    p.dy += (1 - shown[Stretch]) * 26;
    shown[EyeL] *= rig.motion.blink(0); shown[EyeR] *= rig.motion.blink(1);
    switch (rig.theme) {
    case Pet::Mechanical: mechanical(p, shown); break;
    case Pet::Paper: paper(p, shown); break;
    case Pet::Glass: glass(p, shown); break;
    default: break;
    }
}
} // namespace

bool supports(Pet::Theme theme)
{
    return theme == Pet::Mechanical || theme == Pet::Paper || theme == Pet::Glass;
}

lv_obj_t *create(lv_obj_t *parent)
{
    prepare_materials(parent);
    rig.object = lv_obj_create(parent);
    lv_obj_remove_style_all(rig.object);
    lv_obj_remove_flag(rig.object, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(rig.object, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_flag(rig.object, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_event_cb(rig.object, draw, LV_EVENT_DRAW_MAIN, nullptr);
    return rig.object;
}

void select(Pet::Theme theme)
{
    rig.theme = theme;
    lv_obj_update_flag(rig.object, LV_OBJ_FLAG_HIDDEN, !supports(theme));
    rig.motion.reset(theme);
}

void animate(Pet::State state, float dt, float transition_seconds, float touch)
{
    rig.motion.advance(state, dt, transition_seconds, touch);
    lv_obj_invalidate(rig.object);
}
} // namespace Character
