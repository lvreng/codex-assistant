#include "character_cache.h"
#include "generated/character_assets.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <iterator>

namespace Character {
namespace {
constexpr float Pi = 3.14159265359f;
constexpr float LargeScale = 342.0f / 320;
constexpr float PaperScale = 292.0f / 320;
constexpr int MaxPoses = 25;
constexpr float StepDegrees = .25f;
constexpr int LidSteps = 16;

struct Material {
    const lv_image_dsc_t *source;
    float width, height;
    int steps;
    lv_image_dsc_t poses[MaxPoses]{};
};

Material materials[] = {
    {&Assets::metal_shell, 388 * LargeScale, 282 * LargeScale, 6},
    {&Assets::glass_shell, 388 * LargeScale, 277 * LargeScale, 12},
    {&Assets::paper_head, 242 * PaperScale, 193 * PaperScale, 12},
    {&Assets::paper_body, 113 * PaperScale, 125 * PaperScale, 5},
    {&Assets::eye_white, 98 * LargeScale, 98 * LargeScale, 0},
    {&Assets::eye_white, 88 * LargeScale, 88 * LargeScale, 0},
    {&Assets::paper_eye, 78 * PaperScale, 78 * PaperScale, 0},
    {&Assets::metal_lid, 98 * LargeScale, 98 * LargeScale, 0},
    {&Assets::glass_lid, 88 * LargeScale, 88 * LargeScale, 0},
    {&Assets::paper_lid, 78 * PaperScale, 78 * PaperScale, 0},
    {&Assets::shadow, 368 * LargeScale, 29 * LargeScale, 0},
    {&Assets::shadow, 170 * PaperScale, 29 * PaperScale, 0},
};

struct LidMaterial {
    const lv_image_dsc_t *source;
    lv_image_dsc_t openings[LidSteps + 1]{};
};

LidMaterial lids[] = {
    {&Assets::metal_lid},
    {&Assets::glass_lid},
    {&Assets::paper_lid},
};
bool prepared = false;

bool build_pose(lv_obj_t *parent, Material &material, int index)
{
    const auto &source = *material.source;
    const float degrees = (index - material.steps) * StepDegrees;
    const float radians = degrees * Pi / 180;
    const float c = std::abs(std::cos(radians)), s = std::abs(std::sin(radians));
    // Even extents preserve a stable center as the selected angle changes.
    const int w = (static_cast<int>(std::ceil(material.width * c + material.height * s)) + 5) & ~1;
    const int h = (static_cast<int>(std::ceil(material.height * c + material.width * s)) + 5) & ~1;
    auto *buffer = lv_draw_buf_create(w, h, LV_COLOR_FORMAT_ARGB8888, LV_STRIDE_AUTO);
    if (!buffer) return false;
    auto *canvas = lv_canvas_create(parent);
    lv_obj_add_flag(canvas, LV_OBJ_FLAG_HIDDEN);
    lv_draw_buf_clear(buffer, nullptr);
    lv_canvas_set_draw_buf(canvas, buffer);
    lv_layer_t layer;
    lv_canvas_init_layer(canvas, &layer);
    lv_draw_image_dsc_t d;
    lv_draw_image_dsc_init(&d);
    d.src = material.source;
    d.pivot.x = source.header.w / 2;
    d.pivot.y = source.header.h / 2;
    d.scale_x = std::lround(material.width * 256 / source.header.w);
    d.scale_y = std::lround(material.height * 256 / source.header.h);
    d.rotation = std::lround(degrees * 10);
    d.antialias = 1;
    const int32_t left = w / 2 - d.pivot.x, top = h / 2 - d.pivot.y;
    const lv_area_t area{left, top, left + static_cast<int32_t>(source.header.w) - 1,
                         top + static_cast<int32_t>(source.header.h) - 1};
    lv_draw_image(&layer, &d, &area);
    lv_canvas_finish_layer(canvas, &layer);

    // Native RGB565+A8 blits skip the expensive software transform path.
    auto *data = static_cast<uint8_t *>(lv_malloc(w * h * 3));
    if (data) {
        auto *colors = reinterpret_cast<uint16_t *>(data);
        auto *alpha = data + w * h * 2;
        for (int y = 0; y < h; ++y) {
            const auto *row = reinterpret_cast<const lv_color32_t *>(
                buffer->data + y * buffer->header.stride);
            for (int x = 0; x < w; ++x) {
                const auto color = row[x];
                colors[y * w + x] = ((color.red >> 3) << 11) |
                                    ((color.green >> 2) << 5) | (color.blue >> 3);
                alpha[y * w + x] = color.alpha;
            }
        }
        auto &pose = material.poses[index];
        pose.header.magic = LV_IMAGE_HEADER_MAGIC;
        pose.header.cf = LV_COLOR_FORMAT_RGB565A8;
        pose.header.w = w;
        pose.header.h = h;
        pose.header.stride = w * 2;
        pose.data_size = w * h * 3;
        pose.data = data;
    }
    lv_obj_delete(canvas);
    lv_draw_buf_destroy(buffer);
    return data != nullptr;
}

bool build_lid_opening(LidMaterial &lid, int index)
{
    const auto &source = *lid.source;
    if (source.header.cf != LV_COLOR_FORMAT_RGB565A8) return false;
    const size_t pixels = static_cast<size_t>(source.header.w) * source.header.h;
    auto *data = static_cast<uint8_t *>(lv_malloc(pixels * 3));
    if (!data) return false;
    std::memcpy(data, source.data, pixels * 3);

    const auto *source_alpha = source.data + pixels * 2;
    auto *alpha = data + pixels * 2;
    const float opening = index / static_cast<float>(LidSteps);
    const float center = source.header.h * .5f;
    const float half_height = source.header.h * .5f;
    for (uint32_t y = 0; y < source.header.h; ++y) {
        const float distance = std::abs((y + .5f) - center);
        const float coverage = std::clamp(distance - opening * half_height + .5f,
                                          0.0f, 1.0f);
        for (uint32_t x = 0; x < source.header.w; ++x) {
            const size_t at = static_cast<size_t>(y) * source.header.w + x;
            alpha[at] = static_cast<uint8_t>(
                std::lround(source_alpha[at] * coverage));
        }
    }

    auto &pose = lid.openings[index];
    pose.header = source.header;
    pose.data_size = pixels * 3;
    pose.data = data;
    return true;
}
} // namespace

void prepare_materials(lv_obj_t *parent)
{
    if (prepared) return;
    prepared = true;
    unsigned total = 0, completed = 0;
    size_t bytes = 0;
    for (const auto &material : materials) total += material.steps * 2 + 1;
    total += static_cast<unsigned>(std::size(lids)) * (LidSteps + 1);
    const uint32_t started = lv_tick_get();
    for (auto &material : materials) {
        for (int i = 0; i <= material.steps * 2; ++i) {
            if (!build_pose(parent, material, i)) {
                LV_LOG_WARN("Material cache allocation failed; using source image");
                return;
            }
            bytes += material.poses[i].data_size;
            ++completed;
            if (completed % 8 == 0 || completed == total) {
                const uint32_t elapsed = lv_tick_elaps(started);
                std::printf("Character cache %u/%u (%u%%), %u ms, ETA %u ms, %u KiB\n",
                            completed, total, completed * 100 / total,
                            static_cast<unsigned>(elapsed),
                            static_cast<unsigned>(elapsed * (total - completed) / completed),
                            static_cast<unsigned>(bytes / 1024));
            }
        }
    }
    for (auto &lid : lids) {
        for (int i = 0; i <= LidSteps; ++i) {
            if (!build_lid_opening(lid, i)) {
                LV_LOG_WARN("Eyelid cache allocation failed; using clipped source image");
                return;
            }
            bytes += lid.openings[i].data_size;
            ++completed;
            if (completed % 8 == 0 || completed == total) {
                const uint32_t elapsed = lv_tick_elaps(started);
                std::printf("Character cache %u/%u (%u%%), %u ms, ETA %u ms, %u KiB\n",
                            completed, total, completed * 100 / total,
                            static_cast<unsigned>(elapsed),
                            static_cast<unsigned>(elapsed * (total - completed) / completed),
                            static_cast<unsigned>(bytes / 1024));
            }
        }
    }
}

const lv_image_dsc_t *material_pose(const lv_image_dsc_t &source,
                                   float width, float height, float radians)
{
    for (const auto &material : materials) {
        if (material.source != &source || std::abs(width - material.width) > .6f ||
            std::abs(height - material.height) > .6f)
            continue;
        const int angle = std::clamp(static_cast<int>(std::lround(
                                         radians * 180 / (Pi * StepDegrees))),
                                     -material.steps, material.steps);
        const auto &pose = material.poses[angle + material.steps];
        return pose.data ? &pose : nullptr;
    }
    return nullptr;
}

const lv_image_dsc_t *eyelid_pose(const lv_image_dsc_t &source,
                                  float opening, float *shown_opening)
{
    const int index = std::clamp(
        static_cast<int>(std::lround(opening * LidSteps)), 0, LidSteps);
    if (shown_opening) *shown_opening = index / static_cast<float>(LidSteps);
    for (const auto &lid : lids) {
        if (lid.source != &source) continue;
        const auto &pose = lid.openings[index];
        return pose.data ? &pose : nullptr;
    }
    return nullptr;
}
} // namespace Character
