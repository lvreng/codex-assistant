#pragma once
#include <cstdint>

// Model IDs match the existing wire protocol. No LVGL or heap allocation here.
namespace Thinker {
constexpr float Duration = 2.60f;
constexpr float InterruptedDuration = 0.65f;
struct Vec3 { float x, y, z; };
struct Camera { Vec3 position; float focal, yaw, pitch; };
// Fixed world points and a single camera govern every image layer.
Camera camera(uint8_t from, uint8_t to, float progress);
Vec3 body_position(uint8_t model);
Vec3 project(const Camera &camera, Vec3 world);
void render565(const uint16_t *from_pixels, const uint16_t *to_pixels, uint16_t *output,
               int width, int height, uint8_t from, uint8_t to, float progress);
void render888(const uint32_t *from_pixels, const uint32_t *to_pixels, uint32_t *output,
               int width, int height, uint8_t from, uint8_t to, float progress);
}
