#include "tear_free_display.h"
#include "device_link.h"
#include "display_buffer_pool.h"
#include "display_damage.h"
#include "driver/ppa.h"
#include "esp_heap_caps.h"
#include "esp_cache.h"
#include "esp_lcd_mipi_dsi.h"
#include "esp_lvgl_port.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include <algorithm>
#include <atomic>
#include <cstdio>

namespace TearFreeDisplay {
namespace {
constexpr char Tag[] = "display_present";
esp_lcd_panel_handle_t panel = nullptr;
ppa_client_handle_t rotator = nullptr;
uint8_t *canvas = nullptr;
void *buffers[3] = {};
int width = 0, height = 0;
size_t bytes = 0;
SemaphoreHandle_t boundary = nullptr;
DisplayBuffers::Pool pool;
DisplayDamage::Tracker damage;
bool partial_enabled = true;
bool partial_verified[3] = {};
std::atomic<uint32_t> events{0}, submitted{0}, waited{0}, errors{0}, max_rotate_us{0};
std::atomic<uint32_t> rotation_checked{0}, rotation_errors{0};
std::atomic<uint32_t> partial_frames{0}, partial_checks{0}, partial_errors{0};
std::atomic<uint32_t> rotated_pixels{0};

bool IRAM_ATTR completed(esp_lcd_panel_handle_t, esp_lcd_dpi_panel_event_data_t *, void *)
{
    events.fetch_add(1, std::memory_order_relaxed);
    BaseType_t woke = pdFALSE;
    xSemaphoreGiveFromISR(boundary, &woke);
    return woke == pdTRUE;
}

void wake(lv_event_t *)
{
    lvgl_port_task_wake(LVGL_PORT_EVENT_DISPLAY, nullptr);
}

void flush(lv_display_t *display, const lv_area_t *area, uint8_t *)
{
    damage.invalidate({area->x1, area->y1, area->x2, area->y2});
    if (!lv_display_flush_is_last(display)) {
        lv_display_flush_ready(display);
        return;
    }
    int back = pool.find_free(events.load(std::memory_order_relaxed));
    const int64_t deadline = esp_timer_get_time() + 100000;
    while (back < 0 && esp_timer_get_time() < deadline) {
        ++waited;
        xSemaphoreTake(boundary, pdMS_TO_TICKS(20));
        back = pool.find_free(events.load(std::memory_order_relaxed));
    }
    if (back < 0) {
        ++errors;
        ESP_LOGE(Tag, "no retired DPI buffer; keeping previous complete frame");
        lv_display_flush_ready(display);
        return;
    }

    // Each OFFSCREEN buffer receives all damage since its own last update.
    auto region = partial_enabled ? damage.pending(back) : damage.full();
    if (region.empty()) region = damage.full();
    const auto rotated_region = damage.rotated(region);
    ppa_srm_oper_config_t operation{};
    operation.in.buffer = canvas;
    operation.in.pic_w = height;
    operation.in.pic_h = width;
    operation.in.block_w = region.width();
    operation.in.block_h = region.height();
    operation.in.block_offset_x = region.x1;
    operation.in.block_offset_y = region.y1;
    operation.in.srm_cm = PPA_SRM_COLOR_MODE_RGB565;
    operation.out.buffer = buffers[back];
    operation.out.buffer_size = bytes;
    operation.out.pic_w = width;
    operation.out.pic_h = height;
    operation.out.block_offset_x = rotated_region.x1;
    operation.out.block_offset_y = rotated_region.y1;
    operation.out.srm_cm = PPA_SRM_COLOR_MODE_RGB565;
    operation.rotation_angle = PPA_SRM_ROTATION_ANGLE_90;
    operation.scale_x = operation.scale_y = 1;
    operation.mode = PPA_TRANS_MODE_BLOCKING;
    const int64_t started = esp_timer_get_time();
    esp_err_t result = ppa_do_scale_rotate_mirror(rotator, &operation);
    bool partial = region.pixels() < width*height;
    // Retry the full canvas before submission if a partial operation fails.
    auto full_rotation = [&]() {
        operation.in.block_w = height;
        operation.in.block_h = width;
        operation.in.block_offset_x = operation.in.block_offset_y = 0;
        operation.out.block_offset_x = operation.out.block_offset_y = 0;
        region = damage.full();
        partial = false;
        return ppa_do_scale_rotate_mirror(rotator, &operation);
    };
    if (result != ESP_OK && partial) {
        partial_enabled = false;
        ++partial_errors;
        result = full_rotation();
    }
    const auto cost = uint32_t(esp_timer_get_time()-started);
    if (cost > max_rotate_us.load()) max_rotate_us.store(cost);
    if (result == ESP_OK && (!rotation_checked.load() || (partial && !partial_verified[back]))) {
        ESP_ERROR_CHECK(esp_cache_msync(buffers[back], bytes, ESP_CACHE_MSYNC_FLAG_DIR_M2C));
        const auto *source = reinterpret_cast<const uint16_t *>(canvas);
        const auto *rotated = static_cast<const uint16_t *>(buffers[back]);
        uint32_t different = 0;
        for (int y = 0; y < width; ++y) for (int x = 0; x < height; ++x)
            if (source[y*height+x] != rotated[(height-1-x)*width+y]) ++different;
        if (partial) {
            ++partial_checks;
            partial_verified[back] = true;
        }
        rotation_checked.fetch_add(uint32_t(width)*height);
        if (different) {
            if (partial) {
                partial_errors.fetch_add(different);
                partial_enabled = false;
                ESP_LOGE(Tag, "partial rotation mismatch; reverting to complete frames: %lu",
                         static_cast<unsigned long>(different));
                result = full_rotation();
            } else {
                rotation_errors.store(different);
                ++errors;
                result = ESP_FAIL;
                ESP_LOGE(Tag, "rotation pixel mismatch: %lu", static_cast<unsigned long>(different));
            }
        }
    }
    if (result == ESP_OK)
        result = esp_lcd_panel_draw_bitmap(panel, 0, 0, width, height, buffers[back]);
    if (result == ESP_OK) {
        // The driver latches the selected pointer at its DMA frame boundary.
        // Its callback has no buffer id; two boundaries conservatively cover a
        // callback racing with submission before the old buffer is reused.
        pool.submitted(back, events.load(std::memory_order_relaxed));
        damage.completed(back);
        rotated_pixels.fetch_add(region.pixels());
        if (partial) ++partial_frames;
        ++submitted;
    } else {
        ++errors;
        ESP_LOGE(Tag, "frame presentation failed: %s", esp_err_to_name(result));
    }
    lv_display_flush_ready(display);
}
} // namespace

lv_display_t *create(esp_lcd_panel_handle_t handle, int native_width, int native_height)
{
    panel = handle;
    width = native_width;
    height = native_height;
    bytes = size_t(width) * height * sizeof(uint16_t);
    damage.reset(height, width);
    canvas = static_cast<uint8_t *>(heap_caps_aligned_calloc(
        CONFIG_CACHE_L2_CACHE_LINE_SIZE, bytes, 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    boundary = xSemaphoreCreateBinary();
    if (!canvas || !boundary) return nullptr;
    ESP_ERROR_CHECK(esp_lcd_dpi_panel_get_frame_buffer(panel, 3, &buffers[0], &buffers[1], &buffers[2]));
    ppa_client_config_t config{};
    config.oper_type = PPA_OPERATION_SRM;
    ESP_ERROR_CHECK(ppa_register_client(&config, &rotator));
    esp_lcd_dpi_panel_event_callbacks_t callbacks{};
    callbacks.on_frame_buf_complete = completed;
    ESP_ERROR_CHECK(esp_lcd_dpi_panel_register_event_callbacks(panel, &callbacks, nullptr));
    lv_display_t *display = lv_display_create(width, height);
    if (!display) return nullptr;
    lv_display_set_color_format(display, LV_COLOR_FORMAT_RGB565);
    // DIRECT keeps the draw buffer stride; configure orientation before sizing it.
    lv_display_set_rotation(display, LV_DISPLAY_ROTATION_90);
    lv_display_set_buffers(display, canvas, nullptr, bytes, LV_DISPLAY_RENDER_MODE_DIRECT);
    const auto *draw_buffer = lv_display_get_buf_active(display);
    if (!draw_buffer || draw_buffer->header.stride != uint32_t(height)*sizeof(uint16_t)) {
        ESP_LOGE(Tag, "landscape canvas stride mismatch");
        return nullptr;
    }
    lv_display_set_flush_cb(display, flush);
    lv_display_add_event_cb(display, wake, LV_EVENT_REFR_REQUEST, nullptr);
    ESP_LOGI(Tag, "RGB565 direct canvas, damage-tracked PPA rotation, triple DPI buffers, frame-boundary retirement");
    return display;
}

void report()
{
    DeviceLink::printf("@PRESENT submitted=%lu boundaries=%lu waits=%lu errors=%lu rotate_max_ms=%.2f "
                "rotation_checked=%lu rotation_errors=%lu partial_frames=%lu partial_checks=%lu "
                "partial_errors=%lu rotated_pixels=%lu\n",
                static_cast<unsigned long>(submitted.load()), static_cast<unsigned long>(events.load()),
                static_cast<unsigned long>(waited.load()), static_cast<unsigned long>(errors.load()),
                max_rotate_us.load()/1000.0, static_cast<unsigned long>(rotation_checked.load()),
                static_cast<unsigned long>(rotation_errors.load()),
                static_cast<unsigned long>(partial_frames.load()),
                static_cast<unsigned long>(partial_checks.load()),
                static_cast<unsigned long>(partial_errors.load()),
                static_cast<unsigned long>(rotated_pixels.load()));
}
} // namespace TearFreeDisplay
