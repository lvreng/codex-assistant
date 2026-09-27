#include "sd_media_player.h"
#include "device_link.h"
#include "sd_camera.h"
#include "astra_timing.h"
#include "astra_compose.h"
#include "sd_read_window.h"
#include "media_crc.h"

#include "driver/jpeg_decode.h"
#include "driver/sdmmc_host.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_rom_crc.h"
#include "esp_timer.h"
#include "esp_vfs_fat.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "sd_pwr_ctrl_by_on_chip_ldo.h"
#include "sdmmc_cmd.h"

#include <algorithm>
#include <cstdio>
#include <cstring>
#include <sys/stat.h>
#include <unistd.h>

namespace SdMediaPlayer {
namespace {
constexpr char Tag[] = "sd_media";
constexpr uint16_t Width = 560;
constexpr uint16_t Height = 416;
constexpr size_t RgbBytes = size_t(Width) * Height * 2;
constexpr size_t HeaderBytes = 32;
constexpr size_t EntryBytes = 12;
constexpr size_t MaxFrameBytes = SdCamera::MaxFrameBytes;
constexpr uint32_t MaxMediaFileBytes = 512U * 1024U * 1024U;
constexpr int64_t StorageProbePeriodUs = 500000;
constexpr uint8_t MediaBegin = 1;
constexpr uint8_t MediaChunk = 2;
constexpr uint8_t MediaEnd = 3;
constexpr uint8_t MediaAbort = 4;
constexpr uint8_t MediaQuery = 5;
constexpr uint8_t InvalidId = 255;

struct Clip {
    FILE *file = nullptr;
    uint8_t *index = nullptr;
    uint32_t frame_count = 0;
    uint16_t fps = 30;
    uint32_t frame = 0;
    uint8_t id = InvalidId;
    uint8_t model = 0;
    uint8_t source = 0;
    int64_t started_us = 0;
    uint32_t shown_frames = 0;
};

struct Upload {
    FILE *file = nullptr;
    uint8_t id = InvalidId;
    uint32_t expected_size = 0;
    uint32_t expected_crc = 0;
    uint32_t offset = 0;
    uint32_t crc = 0;
    uint32_t last_offset = 0;
    uint16_t last_length = 0;
    uint32_t last_data_crc = 0;
    bool has_last_chunk = false;
    bool active = false;
};

portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
sdmmc_card_t *card = nullptr;
sd_pwr_ctrl_handle_t power = nullptr;
jpeg_decoder_handle_t decoder = nullptr;
TaskHandle_t task = nullptr;
SemaphoreHandle_t io_mutex = nullptr;
uint8_t *jpeg = nullptr;
uint8_t *rgb[2] = {};
size_t rgb_capacity[2] = {};
const uint8_t *displayed_pixels = nullptr;
uint8_t *transition_source = nullptr, *transition_input = nullptr;
size_t transition_capacity = 0;
uint8_t rendered_model = 0, settled_model = 0;
bool entry_join = false;
bool loop_entry_pending = false;
uint32_t resume_frame = 0;
uint8_t resume_clip = InvalidId;
uint32_t request_generation = 0, transition_count = 0, transition_max_us = 0;
// Keep filesystem CRC work off the serial task stack. FATFS/stdIO already
// consume a meaningful amount of stack while opening and scanning a file.
uint8_t file_crc_buffer[4096] = {};
uint8_t front = 0;
bool frame_ready = false;
bool copying_frame = false;
enum class FrameKind { Normal, CameraEnd, LoopEntry };
FrameKind frame_kind = FrameKind::Normal;
uint32_t consumed_end_sequence = 0;
bool consumed_end_exact = false;
bool mounted = false;
bool storage_fault = false;
bool initialized = false;
bool enabled = false;
uint32_t local_frame_sequence = 0;
uint32_t local_decoded_frames = 0;
uint8_t requested_model = 0;
uint8_t requested_style = 1;
uint16_t requested_astra_minimum[2] = {50, 100};
AstraTiming::Clock astra_clock;
AstraTiming::Clock astra_background_clock;
uint8_t *astra_cache[2] = {};
size_t astra_capacity[2] = {};
uint32_t astra_cached_frame[2] = {UINT32_MAX, UINT32_MAX};
uint32_t astra_epoch = 0, astra_outputs = 0, astra_blends = 0;
uint64_t astra_position_milli = 0;
uint64_t astra_background_milli = 0;
bool astra_layers_ready = false;
uint32_t astra_compose_max_us = 0;
uint64_t astra_decode_total_us = 0, astra_compose_total_us = 0;
uint64_t astra_read_total_us = 0, astra_jpeg_total_us = 0;
uint64_t astra_crc_total_us = 0;
uint32_t astra_rate_milli = 1000, astra_blend_max_us = 0;
uint8_t astra_clip = InvalidId;
Pet::State requested_state = Pet::Offline;
int64_t next_frame_us = 0;
int64_t next_storage_probe_us = 0;
Clip clip{};
Clip landing{};
Clip astra_background{}, astra_light{};
Upload upload_state{};
bool uploading = false;

uint16_t read16(const uint8_t *p)
{
    return uint16_t(p[0]) | (uint16_t(p[1]) << 8);
}

uint32_t read32(const uint8_t *p)
{
    return uint32_t(p[0]) | (uint32_t(p[1]) << 8) |
           (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}

uint32_t crc32(const uint8_t *data, size_t size)
{
    static bool checked = false, optimized = true;
    if (!optimized) return esp_rom_crc32_le(0, data, size);
    const auto started = esp_timer_get_time();
    const auto result = MediaCrc::update(0, data, size);
    if (!checked && size >= 32768) {
        const auto fast_done = esp_timer_get_time();
        const auto reference = esp_rom_crc32_le(0, data, size);
        const auto done = esp_timer_get_time();
        checked = true;
        optimized = reference == result;
        DeviceLink::printf("@MEDIA_CRC checked=%u bytes=%u fast_us=%lld rom_us=%lld\n", unsigned(optimized),
                    unsigned(size), static_cast<long long>(fast_done-started),
                    static_cast<long long>(done-fast_done));
        return reference;
    }
    return result;
}

void close_clip(Clip &item = clip)
{
    if (item.file) std::fclose(item.file);
    if (item.index) heap_caps_free(item.index);
    item = Clip{};
    if (&item == &clip) {
        astra_clock.valid = false;
        astra_background_clock.valid = false;
        astra_cached_frame[0] = astra_cached_frame[1] = UINT32_MAX;
    }
}

void disable_storage(const char *reason)
{
    // Never expose a decoded frame after the SD path has failed.
    portENTER_CRITICAL(&mux);
    frame_ready = false;
    portEXIT_CRITICAL(&mux);
    storage_fault = true;
    mounted = false;
    close_clip();
    close_clip(landing);
    close_clip(astra_background);
    close_clip(astra_light);
    ESP_LOGW(Tag, "SD local media disabled: %s", reason ? reason : "I/O failure");
}

bool mount_card()
{
    if (mounted) return true;
    const sd_pwr_ctrl_ldo_config_t power_config = {.ldo_chan_id = 4};
    if (sd_pwr_ctrl_new_on_chip_ldo(&power_config, &power) != ESP_OK) {
        ESP_LOGW(Tag, "cannot create SD LDO4");
        return false;
    }
    sdmmc_host_t host = SDMMC_HOST_DEFAULT();
    host.slot = SDMMC_HOST_SLOT_0;
    host.max_freq_khz = SDMMC_FREQ_HIGHSPEED;
    host.flags = SDMMC_HOST_FLAG_4BIT;
    host.pwr_ctrl_handle = power;
    sdmmc_slot_config_t slot = SDMMC_SLOT_CONFIG_DEFAULT();
    slot.width = 4;
    slot.clk = GPIO_NUM_43;
    slot.cmd = GPIO_NUM_44;
    slot.d0 = GPIO_NUM_39;
    slot.d1 = GPIO_NUM_40;
    slot.d2 = GPIO_NUM_41;
    slot.d3 = GPIO_NUM_42;
    esp_vfs_fat_mount_config_t config = {
        .format_if_mount_failed = false,
        .max_files = 4,
        .allocation_unit_size = 16 * 1024,
        .disk_status_check_enable = true,
        .use_one_fat = false,
    };
    if (esp_vfs_fat_sdmmc_mount("/sdcard", &host, &slot, &config, &card) != ESP_OK) {
        ESP_LOGW(Tag, "SD mount failed");
        return false;
    }
    mounted = true;
    ESP_LOGI(Tag, "SD mounted, bus=%u kHz sector=%u", unsigned(card->max_freq_khz), unsigned(card->csd.sector_size));
    return true;
}

uint8_t selected_clip(uint8_t model, Pet::State state, uint8_t style)
{
    const bool working = state == Pet::Thinking || state == Pet::Working ||
                         state == Pet::Searching;
    if (model == 1 && (working || state == Pet::Done)) return state == Pet::Done ? 1 : 0;
    if (model == 2 && (working || state == Pet::Done)) return state == Pet::Done ? 3 : 2;
    if (model == 3 && (working || state == Pet::Done)) return state == Pet::Done ? 5 : 4;
    if (model == 4 && (working || state == Pet::Done)) {
        if (state == Pet::Done) return 8;
        return style == 0 ? 6 : 7;
    }
    return InvalidId;
}

bool read_entry(const Clip &item, uint32_t number, uint32_t *offset, uint32_t *length, uint32_t *checksum)
{
    if (!item.index || number >= item.frame_count) return false;
    const uint8_t *entry = item.index + number * EntryBytes;
    *offset = read32(entry);
    *length = read32(entry + 4);
    *checksum = read32(entry + 8);
    return *length > 0 && *length <= MaxFrameBytes;
}

bool open_clip(Clip &item, uint8_t id, uint8_t model, uint8_t source = 0, bool layer = false)
{
    if (!mounted || model < 1 || model > 4 || id > 8 ||
        (source && (source > 4 || source == model))) return false;
    if (item.file && item.id == id && item.model == model && item.source == source) return true;
    char path[24];
    if (source) std::snprintf(path, sizeof(path), "/sdcard/T%u%u%u.CJP", source, model, id);
    else std::snprintf(path, sizeof(path), "/sdcard/%c%02u.CJP", layer ? 'A' : 'C', id);
    FILE *file = std::fopen(path, "rb");
    if (!file) {
        if (layer) return false;
        if (source) {
            ESP_LOGW(Tag, "SD camera clip missing: %s; retaining local loops", path);
            return false;
        }
        disable_storage("clip open failed");
        return false;
    }
    // Avoid stdio read-ahead moving an aligned SD DMA request into an unaligned buffer.
    std::setvbuf(file, nullptr, _IONBF, 0);
    close_clip(item);
    uint8_t header[HeaderBytes];
    if (std::fread(header, 1, HeaderBytes, file) != HeaderBytes ||
        std::memcmp(header, source ? "CJP4T2\0\0" : "CJP4V1\0\0", 8) != 0 ||
        read16(header + 8) != Width || read16(header + 10) != Height ||
        read16(header + 12) == 0 || read16(header + 12) > 60 ||
        header[14] != model || (source ? header[15] != id : header[15] > 1) ||
        read16(header + 16) > (source ? 0 : 1) || read16(header + 18) != source ||
        read32(header + 28) != crc32(header, 28)) {
        std::fclose(file);
        disable_storage("clip header invalid");
        return false;
    }
    const uint32_t frames = read32(header + 20);
    const size_t index_bytes = size_t(frames) * EntryBytes;
    if (frames < 2 || frames > 3600) {
        std::fclose(file);
        disable_storage("clip frame count invalid");
        return false;
    }
    uint8_t *index = static_cast<uint8_t *>(
        heap_caps_malloc(index_bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (!index || std::fread(index, 1, index_bytes, file) != index_bytes ||
        crc32(index, index_bytes) != read32(header + 24)) {
        if (index) heap_caps_free(index);
        std::fclose(file);
        disable_storage("clip index read failed");
        return false;
    }
    std::fseek(file, 0, SEEK_END);
    const long file_size = std::ftell(file);
    if (file_size < 0 || static_cast<uint64_t>(file_size) > MaxMediaFileBytes) {
        heap_caps_free(index);
        std::fclose(file);
        disable_storage("clip size invalid");
        return false;
    }
    uint32_t expected = HeaderBytes + index_bytes;
    for (uint32_t i = 0; i < frames; ++i) {
        const uint8_t *entry = index + i * EntryBytes;
        const uint32_t offset = read32(entry);
        const uint32_t length = read32(entry + 4);
        if (offset != expected || !length || length > MaxFrameBytes ||
            offset + length > uint32_t(file_size)) {
            heap_caps_free(index);
            std::fclose(file);
            disable_storage("clip layout invalid");
            return false;
        }
        expected = offset + length;
    }
    if (expected != uint32_t(file_size)) {
        heap_caps_free(index);
        std::fclose(file);
        disable_storage("clip size invalid");
        return false;
    }
    item.file = file;
    item.index = index;
    item.frame_count = frames;
    item.fps = read16(header + 12);
    item.frame = 0;
    item.id = id;
    item.model = model;
    item.source = source;
    if (&item == &clip) next_frame_us = 0;
    return true;
}

bool decode_image(Clip &item, uint8_t *destination, size_t capacity)
{
    if (!item.file) return false;
    uint32_t offset, length, expected_crc;
    const int64_t read_started = esp_timer_get_time();
    if (!read_entry(item, item.frame, &offset, &length, &expected_crc)) return false;
    const auto window = SdReadWindow::aligned(offset, length);
    if (std::fseek(item.file, window.offset, SEEK_SET) != 0 ||
        std::fread(jpeg, 1, window.bytes, item.file) < window.prefix + length) {
        disable_storage("SD read failed");
        ESP_LOGW(Tag, "SD read failed; local media disabled");
        return false;
    }
    if (window.prefix) std::memmove(jpeg, jpeg+window.prefix, length);
    const int64_t crc_started = esp_timer_get_time();
    if (crc32(jpeg, length) != expected_crc) {
        disable_storage("SD frame CRC failed");
        ESP_LOGW(Tag, "SD frame CRC failed; local media disabled");
        return false;
    }
    jpeg_decode_picture_info_t info{};
    jpeg_decode_cfg_t config{};
    config.output_format = JPEG_DECODE_OUT_FORMAT_RGB565;
    config.rgb_order = JPEG_DEC_RGB_ELEMENT_ORDER_BGR;
    uint32_t decoded = 0;
    const int64_t decode_started = esp_timer_get_time();
    if (jpeg_decoder_get_info(jpeg, length, &info) != ESP_OK ||
        info.width != Width || info.height != Height ||
        jpeg_decoder_process(decoder, &config, jpeg, length, destination,
                             capacity, &decoded) != ESP_OK ||
        decoded != RgbBytes)
        return false;
    if (&item == &astra_background || &item == &astra_light) {
        const int64_t done = esp_timer_get_time();
        portENTER_CRITICAL(&mux);
        astra_read_total_us += decode_started-read_started;
        astra_crc_total_us += decode_started-crc_started;
        astra_jpeg_total_us += done-decode_started;
        portEXIT_CRITICAL(&mux);
    }
    return true;
}

bool astra_image(uint8_t *destination, size_t capacity, uint16_t minimum, int64_t now)
{
    const bool layers = astra_cache[0] && astra_cache[1] &&
        open_clip(astra_background, 0, 4, 0, true) && open_clip(astra_light, clip.id, 4, 0, true);
    portENTER_CRITICAL(&mux);
    astra_layers_ready = layers;
    portEXIT_CRITICAL(&mux);
    // Old cards remain playable at their original speed, never retimed as a whole.
    if (!layers) return mounted && decode_image(clip, destination, capacity);
    if (astra_background.frame_count != clip.frame_count || astra_light.frame_count != clip.frame_count ||
        astra_background.fps != clip.fps || astra_light.fps != clip.fps) {
        disable_storage("Astra layer timing does not match the loop");
        return false;
    }
    const double rate = AstraTiming::speed(clip.id == 6 ? 0 : 1, minimum);
    if (!astra_clock.valid) {
        astra_clock.reset(now, clip.frame, rate);
        astra_background_clock.reset(now, clip.frame, 1);
        portENTER_CRITICAL(&mux);
        ++astra_epoch;
        portEXIT_CRITICAL(&mux);
    }
    const auto sample = astra_clock.sample(now, clip.fps, rate, clip.frame_count);
    const auto background = astra_background_clock.sample(now, clip.fps, 1, clip.frame_count);
    clip.frame = astra_background.frame = background.first;
    if (!decode_image(astra_background, destination, capacity)) return false;
    const int first = astra_cached_frame[1] == sample.first ? 1 : 0;
    const bool blended = rate < 1 && sample.alpha;
    const int second = blended ? 1-first : first;
    astra_light.frame = sample.first;
    if (astra_cached_frame[first] != sample.first) {
        if (!decode_image(astra_light, astra_cache[first], astra_capacity[first])) return false;
        astra_cached_frame[first] = sample.first;
    }
    if (blended && astra_cached_frame[second] != sample.second) {
        astra_light.frame = sample.second;
        if (!decode_image(astra_light, astra_cache[second], astra_capacity[second])) return false;
        astra_cached_frame[second] = sample.second;
    }
    const auto started = esp_timer_get_time();
    AstraCompose::compose(reinterpret_cast<uint16_t *>(destination),
                         reinterpret_cast<uint16_t *>(astra_cache[first]),
                         reinterpret_cast<uint16_t *>(astra_cache[second]), Width * Height,
                         blended ? sample.alpha : 0);
    const uint32_t compose_us = esp_timer_get_time()-started;
    portENTER_CRITICAL(&mux);
    astra_position_milli = uint64_t(astra_clock.position * 1000);
    astra_background_milli = uint64_t(astra_background_clock.position * 1000);
    astra_rate_milli = uint32_t(rate * 1000 + .5);
    astra_clip = clip.id;
    ++astra_outputs;
    astra_decode_total_us += started-now;
    astra_compose_total_us += compose_us;
    if (blended) ++astra_blends;
    astra_compose_max_us = std::max(astra_compose_max_us, compose_us);
    if (blended) astra_blend_max_us = std::max(astra_blend_max_us, compose_us);
    portEXIT_CRITICAL(&mux);
    return true;
}

bool decode_frame(uint32_t generation, uint16_t astra_minimum = 100, bool prefetch = false)
{
    if (!clip.file || (frame_ready && !prefetch)) return false;
    const int64_t started = esp_timer_get_time();
    const int back = 1 - front;
    const bool camera_clip = clip.source != 0;
    const bool loop_entry = !camera_clip && loop_entry_pending;
    if (camera_clip) {
        if (!clip.started_us) clip.started_us = started;
        clip.frame = SdCamera::sample(started - clip.started_us, clip.frame_count, clip.fps, 1, 1);
    }
    const bool camera_end = camera_clip && clip.frame == clip.frame_count - 1;
    const bool timed_astra = !camera_clip && !loop_entry && !entry_join &&
                            (clip.id == 6 || clip.id == 7);
    if (timed_astra) {
        if (!astra_image(rgb[back], rgb_capacity[back], astra_minimum, started)) return false;
    } else if (!decode_image(clip, rgb[back], rgb_capacity[back])) return false;
    if (camera_clip && clip.frame < 8) {
        SdCamera::mix565(reinterpret_cast<uint16_t *>(transition_source),
                        reinterpret_cast<uint16_t *>(rgb[back]), reinterpret_cast<uint16_t *>(rgb[back]),
                        Width * Height, SdCamera::smooth_weight(float(clip.frame)/8));
    } else if (!camera_clip && entry_join) {
        SdCamera::mix565(reinterpret_cast<uint16_t *>(transition_source),
                        reinterpret_cast<uint16_t *>(rgb[back]), reinterpret_cast<uint16_t *>(rgb[back]),
                        Width * Height, SdCamera::smooth_weight(float(clip.frame)/SdCamera::StateJoinFrames));
    }
    if (camera_end) {
        if (!open_clip(landing, clip.id, clip.model)) return false;
        uint32_t offset, camera_length, camera_crc, loop_length, loop_crc;
        if (!read_entry(clip, clip.frame, &offset, &camera_length, &camera_crc) ||
            !read_entry(landing, 0, &offset, &loop_length, &loop_crc) ||
            camera_length != loop_length || camera_crc != loop_crc) {
            disable_storage("camera final JPEG differs from target loop entry");
            return false;
        }
    }
    if (camera_end) std::memcpy(transition_source, rgb[back], RgbBytes);
    if (prefetch) {
        // Decode only into the other buffer while LVGL consumes the current one.
        // Never publish over an unconsumed frame or an in-flight copy.
        for (;;) {
            portENTER_CRITICAL(&mux);
            const bool pending = frame_ready || copying_frame;
            const bool cancelled = !enabled || generation != request_generation;
            portEXIT_CRITICAL(&mux);
            if (cancelled) return true;
            if (!pending) break;
            vTaskDelay(pdMS_TO_TICKS(1));
        }
    }
    portENTER_CRITICAL(&mux);
    // A model/state request can arrive during JPEG decode or composition.
    // Publish only the generation that was requested, never a stale new-model frame.
    if ((!loop_entry && generation != request_generation) || !enabled) {
        portEXIT_CRITICAL(&mux);
        return true;
    }
    front = uint8_t(back);
    frame_kind = camera_end ? FrameKind::CameraEnd : loop_entry ? FrameKind::LoopEntry : FrameKind::Normal;
    rendered_model = clip.model;
    if (camera_clip) {
        ++transition_count;
        transition_max_us = std::max(transition_max_us, uint32_t(esp_timer_get_time()-started));
    }
    local_frame_sequence++;
    local_decoded_frames++;
    frame_ready = true;
    portEXIT_CRITICAL(&mux);
    ++clip.shown_frames;
    if (camera_end) {
        settled_model = clip.model;
        resume_clip = landing.id;
        resume_frame = landing.frame;
        DeviceLink::printf("@CAMERA end from=%u to=%u frames=%lu output_frames=%lu elapsed_ms=%lld "
                    "landing_clip=%u next_frame=%lu exact=%u\n", clip.source, clip.model,
                    static_cast<unsigned long>(clip.frame_count), static_cast<unsigned long>(clip.shown_frames),
                    static_cast<long long>((esp_timer_get_time()-clip.started_us)/1000),
                    landing.id, static_cast<unsigned long>(landing.frame), 1U);
        close_clip();
        clip = landing;
        landing = Clip{};
        entry_join = false;
        loop_entry_pending = true;
    } else if (!camera_clip) {
        if (loop_entry) {
            const bool equal = std::memcmp(rgb[back], transition_source, RgbBytes) == 0;
            DeviceLink::printf("@LOOP_RESUME clip=%u frame=%lu expected_clip=%u expected_frame=%lu "
                        "joined=0 decoded_entry=1 continuous=%u\n", clip.id,
                        static_cast<unsigned long>(clip.frame), resume_clip,
                        static_cast<unsigned long>(resume_frame), equal);
            loop_entry_pending = false;
            resume_clip = InvalidId;
        }
        settled_model = clip.model;
        if (clip.frame >= SdCamera::StateJoinFrames) entry_join = false;
        ++clip.frame;
        clip.frame %= clip.frame_count;
    }
    return true;
}

// FATFS can defer reporting card removal until the next filesystem operation.
// Probe the open clip at a low rate so a prefetched frame cannot keep the
// artwork alive after the card is removed.
bool probe_storage()
{
    if (!mounted || !clip.file) return mounted;
    const long saved = std::ftell(clip.file);
    if (saved < 0 || std::fseek(clip.file, HeaderBytes, SEEK_SET) != 0) {
        disable_storage("SD removed or seek failed");
        return false;
    }
    uint8_t byte = 0;
    const bool ok = std::fread(&byte, 1, 1, clip.file) == 1;
    if (saved >= 0) std::fseek(clip.file, saved, SEEK_SET);
    clearerr(clip.file);
    if (!ok) {
        disable_storage("SD removed or unreadable");
        return false;
    }
    return true;
}

void playback_task(void *)
{
    AstraCompose::init();
    for (;;) {
        const int64_t probe_now = esp_timer_get_time();
        if (probe_now >= next_storage_probe_us && io_mutex &&
            xSemaphoreTake(io_mutex, pdMS_TO_TICKS(2)) == pdTRUE) {
            next_storage_probe_us = probe_now + StorageProbePeriodUs;
            probe_storage();
            xSemaphoreGive(io_mutex);
        }
        uint8_t model, style;
        uint16_t astra_minimum;
        uint32_t generation;
        Pet::State state;
        bool use;
        portENTER_CRITICAL(&mux);
        model = requested_model;
        style = requested_style;
        astra_minimum = requested_astra_minimum[style];
        generation = request_generation;
        state = requested_state;
        use = enabled;
        const bool is_uploading = uploading;
        const bool ready = frame_ready || copying_frame;
        const bool normal = frame_kind == FrameKind::Normal;
        portEXIT_CRITICAL(&mux);
        const uint8_t id = selected_clip(model, state, style);
        const bool prefetch = ready && normal && clip.file && !clip.source &&
                              !entry_join && !loop_entry_pending &&
                              (id == 6 || id == 7) && clip.id == id && settled_model == model;
        if (!use || !mounted || is_uploading || (id == InvalidId && !loop_entry_pending && !clip.source) ||
            (ready && !prefetch)) {
            if (!use || !mounted || is_uploading) {
                settled_model = 0;
                astra_clock.valid = false;
            }
            vTaskDelay(pdMS_TO_TICKS(5));
            continue;
        }
        if (!io_mutex || xSemaphoreTake(io_mutex, pdMS_TO_TICKS(2)) != pdTRUE) {
            vTaskDelay(pdMS_TO_TICKS(1));
            continue;
        }
        if (!settled_model) {
            if (clip.source) close_clip();
            close_clip(landing);
            entry_join = false;
            resume_clip = InvalidId;
            loop_entry_pending = false;
        }
        if (loop_entry_pending) {
            const int64_t now = esp_timer_get_time();
            // Decode frame zero from the actual destination file. No copy,
            // dissolve, extra hold or intermediate transition is inserted here.
            if (now >= next_frame_us && decode_frame(generation))
                next_frame_us = now + 1000000LL / std::max<uint16_t>(1, clip.fps);
            xSemaphoreGive(io_mutex);
            vTaskDelay(pdMS_TO_TICKS(1));
            continue;
        }
        bool opened = true;
        if (!clip.source) {
            if (settled_model && settled_model != model && transition_source && transition_input) {
                std::memcpy(transition_source, displayed_pixels ? displayed_pixels : rgb[front], RgbBytes);
                SdCamera::soften565(reinterpret_cast<uint16_t *>(transition_source),
                                    reinterpret_cast<uint16_t *>(transition_input), Width, Height);
                close_clip(landing);
                entry_join = true;
                opened = open_clip(clip, id, model, settled_model);
                if (opened) {
                    DeviceLink::printf("@CAMERA start from=%u to=%u fps=%u frames=%lu asset_version=2 target_clip=%u speed=1 duration_ms=%lld\n",
                                settled_model, model, clip.fps, static_cast<unsigned long>(clip.frame_count),
                                clip.id, static_cast<long long>(SdCamera::duration_us(clip.frame_count, clip.fps, 1, 1)/1000));
                }
            } else {
                if (settled_model == model && clip.file && clip.id != id && transition_source) {
                    std::memcpy(transition_source, displayed_pixels ? displayed_pixels : rgb[front], RgbBytes);
                    entry_join = true;
                }
                opened = open_clip(clip, id, model);
            }
        }
        const int64_t now = esp_timer_get_time();
        if (!opened) {
            xSemaphoreGive(io_mutex);
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }
        if (next_frame_us && now < next_frame_us) {
            xSemaphoreGive(io_mutex);
            vTaskDelay(pdMS_TO_TICKS(std::max<int64_t>(1, (next_frame_us - now) / 1000)));
            continue;
        }
        if (!decode_frame(generation, astra_minimum, prefetch)) {
            ESP_LOGW(Tag, "invalid frame in C%02u", id);
            close_clip();
            close_clip(landing);
            xSemaphoreGive(io_mutex);
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }
        const int64_t period = 1000000LL / std::max<uint16_t>(1, clip.fps);
        next_frame_us = next_frame_us ? next_frame_us + period : now + period;
        if (next_frame_us < now - period) next_frame_us = now + period;
        xSemaphoreGive(io_mutex);
    }
}

void media_path(uint8_t id, bool temporary, char *path, size_t size)
{
    if (id >= 10 && id <= 12) {
        const char *names[] = {"A00.CJP", "A06.CJP", "A07.CJP"};
        std::snprintf(path, size, "/sdcard/%s%s", names[id-10], temporary ? ".NEW" : "");
    } else if (id == 9) {
        std::snprintf(path, size, "/sdcard/manifest.json%s",
                      temporary ? ".NEW" : "");
    } else {
        std::snprintf(path, size, "/sdcard/C%02u.CJP%s", id,
                      temporary ? ".NEW" : "");
    }
}

bool file_crc(const char *path, uint32_t *size, uint32_t *checksum)
{
    FILE *file = std::fopen(path, "rb");
    if (!file) return false;
    if (std::fseek(file, 0, SEEK_END) != 0) {
        std::fclose(file);
        return false;
    }
    const long length = std::ftell(file);
    if (length < 0 || static_cast<uint64_t>(length) > MaxMediaFileBytes) {
        std::fclose(file);
        return false;
    }
    std::rewind(file);
    uint32_t crc = 0;
    uint32_t total = 0;
    while (true) {
        const size_t count = std::fread(file_crc_buffer, 1,
                                       sizeof(file_crc_buffer), file);
        if (count) {
            crc = esp_rom_crc32_le(crc, file_crc_buffer, count);
            total += static_cast<uint32_t>(count);
        }
        if (count < sizeof(file_crc_buffer)) {
            if (std::ferror(file)) {
                std::fclose(file);
                return false;
            }
            break;
        }
    }
    std::fclose(file);
    if (total != static_cast<uint32_t>(length)) return false;
    if (size) *size = total;
    if (checksum) *checksum = crc;
    return true;
}

UploadResult upload_error(const char *reason)
{
    UploadResult result;
    result.handled = true;
    result.ok = false;
    std::snprintf(result.reason, sizeof(result.reason), "%s",
                  reason ? reason : "error");
    return result;
}

UploadResult upload_ok(uint32_t offset, uint32_t checksum, bool complete)
{
    UploadResult result;
    result.handled = true;
    result.ok = true;
    result.offset = offset;
    result.crc = checksum;
    result.complete = complete;
    return result;
}

void close_upload(bool remove_temporary)
{
    if (upload_state.file) {
        std::fflush(upload_state.file);
        std::fclose(upload_state.file);
    }
    if (remove_temporary && upload_state.id != InvalidId) {
        char path[40];
        media_path(upload_state.id, true, path, sizeof(path));
        std::remove(path);
    }
    upload_state = Upload{};
    portENTER_CRITICAL(&mux);
    uploading = false;
    portEXIT_CRITICAL(&mux);
}

UploadResult begin_upload(uint8_t id, uint32_t expected_size, uint32_t expected_crc)
{
    if (id > 12 || expected_size > MaxMediaFileBytes)
        return upload_error("invalid file");
    if (upload_state.active) close_upload(false);
    portENTER_CRITICAL(&mux);
    uploading = true;
    portEXIT_CRITICAL(&mux);
    close_clip();
    close_clip(landing);

    close_clip(astra_background);
    close_clip(astra_light);

    char path[40];
    media_path(id, true, path, sizeof(path));
    uint32_t offset = 0;
    uint32_t checksum = 0;
    if (file_crc(path, &offset, &checksum) &&
        (offset > expected_size ||
         (offset == expected_size && checksum != expected_crc))) {
        std::remove(path);
        offset = 0;
        checksum = 0;
    }
    FILE *file = std::fopen(path, offset ? "ab" : "wb");
    if (!file) {
        close_upload(false);
        return upload_error("open temporary file failed");
    }
    upload_state.file = file;
    upload_state.id = id;
    upload_state.expected_size = expected_size;
    upload_state.expected_crc = expected_crc;
    upload_state.offset = offset;
    upload_state.crc = checksum;
    upload_state.active = true;
    return upload_ok(offset, checksum, false);
}

UploadResult query_upload(uint8_t id)
{
    if (id > 12) return upload_error("invalid file");
    char final_path[40];
    media_path(id, false, final_path, sizeof(final_path));
    uint32_t size = 0;
    uint32_t checksum = 0;
    if (file_crc(final_path, &size, &checksum))
        return upload_ok(size, checksum, true);
    char temporary_path[40];
    media_path(id, true, temporary_path, sizeof(temporary_path));
    if (file_crc(temporary_path, &size, &checksum))
        return upload_ok(size, checksum, false);
    return upload_ok(0, 0, false);
}

UploadResult chunk_upload(uint8_t id, uint32_t offset, uint16_t length,
                          uint32_t expected_crc, const uint8_t *data)
{
    if (!upload_state.active || !upload_state.file || upload_state.id != id)
        return upload_error("no active upload");
    // The host retries the same framed packet when an ACK is lost. Make that
    // retry idempotent instead of rejecting it as an out-of-order chunk.
    if (upload_state.has_last_chunk &&
        offset == upload_state.last_offset &&
        length == upload_state.last_length &&
        expected_crc == upload_state.last_data_crc) {
        return upload_ok(upload_state.offset, upload_state.crc, false);
    }
    if (!data || !length || length > Pet::MediaChunkBytes ||
        offset != upload_state.offset ||
        length > upload_state.expected_size - std::min(
            upload_state.expected_size, upload_state.offset))
        return upload_error("invalid chunk bounds");
    if (crc32(data, length) != expected_crc)
        return upload_error("chunk crc mismatch");
    if (std::fwrite(data, 1, length, upload_state.file) != length ||
        std::fflush(upload_state.file) != 0)
        return upload_error("write failed");
    upload_state.last_offset = offset;
    upload_state.last_length = length;
    upload_state.last_data_crc = expected_crc;
    upload_state.has_last_chunk = true;
    upload_state.crc = esp_rom_crc32_le(upload_state.crc, data, length);
    upload_state.offset += length;
    return upload_ok(upload_state.offset, upload_state.crc, false);
}

UploadResult end_upload(uint8_t id, uint32_t expected_size, uint32_t expected_crc)
{
    // END can be retried after the first END ACK was lost. The first call has
    // already renamed the file and cleared the active upload state, so report
    // the completed file again when it matches the requested identity.
    if (!upload_state.active || upload_state.id != id || !upload_state.file) {
        char final_path[40];
        media_path(id, false, final_path, sizeof(final_path));
        uint32_t size = 0;
        uint32_t checksum = 0;
        if (file_crc(final_path, &size, &checksum) &&
            size == expected_size && checksum == expected_crc)
            return upload_ok(size, checksum, true);
        return upload_error("no active upload");
    }
    if (upload_state.offset != expected_size ||
        upload_state.expected_size != expected_size ||
        upload_state.crc != expected_crc) {
        return upload_error("file crc mismatch");
    }
    const int descriptor = fileno(upload_state.file);
    if (std::fflush(upload_state.file) != 0 ||
        (descriptor >= 0 && fsync(descriptor) != 0)) {
        close_upload(false);
        return upload_error("flush failed");
    }
    std::fclose(upload_state.file);
    upload_state.file = nullptr;
    char temporary_path[40];
    char final_path[40];
    media_path(id, true, temporary_path, sizeof(temporary_path));
    media_path(id, false, final_path, sizeof(final_path));
    if (std::rename(temporary_path, final_path) != 0) {
        std::remove(final_path);
        if (std::rename(temporary_path, final_path) != 0) {
            close_upload(false);
            return upload_error("replace failed");
        }
    }
    const uint32_t offset = upload_state.offset;
    const uint32_t checksum = upload_state.crc;
    close_upload(false);
    return upload_ok(offset, checksum, true);
}

UploadResult handle_media(const uint8_t *payload, unsigned size)
{
    if (!payload || size < Pet::MediaControlBytes || payload[0] != 4)
        return UploadResult{};
    const uint8_t operation = payload[1];
    const uint8_t id = payload[2];
    if (!io_mutex || xSemaphoreTake(io_mutex, pdMS_TO_TICKS(250)) != pdTRUE)
        return upload_error("media busy");
    UploadResult result;
    if (operation == MediaBegin && size == Pet::MediaBeginBytes) {
        result = begin_upload(id, read32(payload + 4), read32(payload + 8));
    } else if (operation == MediaChunk && size >= Pet::MediaChunkHeaderBytes) {
        const uint16_t length = read16(payload + 8);
        if (size != Pet::MediaChunkHeaderBytes + length) {
            result = upload_error("invalid chunk length");
        } else {
            result = chunk_upload(id, read32(payload + 4), length,
                                  read32(payload + 10), payload + 14);
        }
    } else if (operation == MediaEnd && size == Pet::MediaBeginBytes) {
        result = end_upload(id, read32(payload + 4), read32(payload + 8));
    } else if (operation == MediaAbort && size == Pet::MediaControlBytes) {
        if (upload_state.active && upload_state.id != id)
            result = upload_error("different upload active");
        else {
            close_upload(true);
            result = upload_ok(0, 0, false);
        }
    } else if (operation == MediaQuery && size == Pet::MediaControlBytes) {
        result = query_upload(id);
    } else {
        result = upload_error("invalid media packet");
    }
    xSemaphoreGive(io_mutex);
    return result;
}
} // namespace

bool init()
{
    if (initialized) return mounted;
    initialized = true;
    storage_fault = false;
    io_mutex = xSemaphoreCreateMutex();
    if (!io_mutex) return false;
    jpeg_decode_engine_cfg_t engine{};
    engine.timeout_ms = 100;
    if (jpeg_new_decoder_engine(&engine, &decoder) != ESP_OK) return false;
    jpeg_decode_memory_alloc_cfg_t input{JPEG_DEC_ALLOC_INPUT_BUFFER};
    size_t input_bytes = 0;
    jpeg = static_cast<uint8_t *>(jpeg_alloc_decoder_mem(MaxFrameBytes + SdReadWindow::ExtraBytes, &input, &input_bytes));
    input.buffer_direction = JPEG_DEC_ALLOC_OUTPUT_BUFFER;
    for (int i = 0; i < 2; ++i)
        rgb[i] = static_cast<uint8_t *>(jpeg_alloc_decoder_mem(RgbBytes, &input, &rgb_capacity[i]));
    transition_input = static_cast<uint8_t *>(jpeg_alloc_decoder_mem(RgbBytes, &input, &transition_capacity));
    transition_source = static_cast<uint8_t *>(heap_caps_malloc(RgbBytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (!transition_input || !transition_source || transition_capacity < RgbBytes) {
        if (transition_input) heap_caps_free(transition_input);
        if (transition_source) heap_caps_free(transition_source);
        transition_input = transition_source = nullptr;
        ESP_LOGW(Tag, "Thinker transition buffers unavailable; local playback retained");
    }
    if (!jpeg || input_bytes < MaxFrameBytes || !rgb[0] || !rgb[1] || rgb_capacity[0] < RgbBytes ||
        rgb_capacity[1] < RgbBytes || !mount_card())
        return false;
    for (int i = 0; i < 2; ++i)
        astra_cache[i] = static_cast<uint8_t *>(jpeg_alloc_decoder_mem(RgbBytes, &input, &astra_capacity[i]));
    if (!astra_cache[0] || !astra_cache[1] || astra_capacity[0] < RgbBytes || astra_capacity[1] < RgbBytes) {
        for (auto &buffer : astra_cache) {
            heap_caps_free(buffer);
            buffer = nullptr;
        }
        ESP_LOGW(Tag, "Astra interpolation buffers unavailable; retaining original-speed native frames");
    }
    // Keep SD decode/composition off the core that renders and presents LVGL.
    return xTaskCreatePinnedToCore(playback_task, "sd_media", 8192, nullptr, 5, &task, 0) == pdPASS;
}

void set_model(uint8_t value)
{
    portENTER_CRITICAL(&mux);
    if (requested_model != value) {
        ++request_generation;
    }
    requested_model = value;
    portEXIT_CRITICAL(&mux);
}

void set_view(Pet::State value)
{
    portENTER_CRITICAL(&mux);
    if (selected_clip(requested_model, requested_state, requested_style) !=
        selected_clip(requested_model, value, requested_style)) {
        ++request_generation;
    }
    requested_state = value;
    portEXIT_CRITICAL(&mux);
}

void set_style(uint8_t value)
{
    portENTER_CRITICAL(&mux);
    value = value > 1 ? 1 : value;
    if (requested_style != value) {
        ++request_generation;
    }
    requested_style = value;
    portEXIT_CRITICAL(&mux);
}

void set_enabled(bool value)
{
    portENTER_CRITICAL(&mux);
    if (enabled != value) {
        ++request_generation;
        frame_ready = false;
    }
    enabled = value;
    portEXIT_CRITICAL(&mux);
}

void set_astra_period(uint8_t style, uint16_t minimum)
{
    if (style > 1) return;
    minimum = AstraTiming::from_minimum(minimum).minimum;
    portENTER_CRITICAL(&mux);
    requested_astra_minimum[style] = minimum;
    portEXIT_CRITICAL(&mux);
}

bool available()
{
    portENTER_CRITICAL(&mux);
    const bool result = mounted && enabled && !uploading &&
                        selected_clip(requested_model, requested_state, requested_style) != InvalidId;
    portEXIT_CRITICAL(&mux);
    return result;
}

bool storage_ready()
{
    return mounted && !storage_fault;
}

bool using_media()
{
    portENTER_CRITICAL(&mux);
    // Opening the next clip temporarily closes the previous FILE. Its complete
    // displayed frame must stay visible until the replacement is decoded.
    // A storage fault/removal still revokes visibility immediately.
    const bool result = mounted && !storage_fault && enabled &&
                        !uploading && local_decoded_frames != 0;
    portEXIT_CRITICAL(&mux);
    return result;
}

uint32_t frame_sequence()
{
    portENTER_CRITICAL(&mux);
    const uint32_t result = local_frame_sequence;
    portEXIT_CRITICAL(&mux);
    return result;
}

uint32_t frames_decoded()
{
    portENTER_CRITICAL(&mux);
    const uint32_t result = local_decoded_frames;
    portEXIT_CRITICAL(&mux);
    return result;
}

uint8_t frame_model()
{
    portENTER_CRITICAL(&mux);
    const uint8_t result = rendered_model;
    portEXIT_CRITICAL(&mux);
    return result;
}

void transition_stats(uint32_t *count, uint32_t *maximum_us)
{
    portENTER_CRITICAL(&mux);
    *count = transition_count;
    *maximum_us = transition_max_us;
    portEXIT_CRITICAL(&mux);
}

bool take_frame(uint8_t *&destination, size_t &capacity, uint32_t *sequence)
{
    if (!destination || capacity < RgbBytes) return false;
    portENTER_CRITICAL(&mux);
    if (!mounted || storage_fault || !enabled || uploading || !frame_ready) {
        portEXIT_CRITICAL(&mux);
        return false;
    }
    const uint8_t selected = front;
    const uint32_t current = local_frame_sequence;
    const FrameKind kind = frame_kind;
    frame_ready = false;
    copying_frame = true;
    // The producer owns the two rgb buffers, LVGL owns its two pictures. Only
    // the retired picture is returned, so decode cannot overwrite a live image.
    std::swap(destination, rgb[selected]);
    std::swap(capacity, rgb_capacity[selected]);
    displayed_pixels = destination;
    portEXIT_CRITICAL(&mux);
    bool equal = false, ordered = false;
    uint32_t checksum = 0;
    if (kind != FrameKind::Normal) {
        equal = std::memcmp(destination, transition_source, RgbBytes) == 0;
        checksum = crc32(destination, RgbBytes);
        if (kind == FrameKind::CameraEnd) {
            consumed_end_sequence = current;
            consumed_end_exact = equal;
        } else {
            ordered = current == consumed_end_sequence + 1 && consumed_end_exact;
        }
    }
    portENTER_CRITICAL(&mux);
    copying_frame = false;
    portEXIT_CRITICAL(&mux);
    if (kind == FrameKind::CameraEnd)
        DeviceLink::printf("@LANDING_FRAME sequence=%lu crc=%08lx pixel_equal=%u\n",
                    static_cast<unsigned long>(current), static_cast<unsigned long>(checksum), equal);
    else if (kind == FrameKind::LoopEntry)
        DeviceLink::printf("@LOOP_HANDOFF sequence=%lu crc=%08lx pixel_equal=%u ordered=%u\n",
                    static_cast<unsigned long>(current), static_cast<unsigned long>(checksum), equal, ordered);
    if (sequence) *sequence = current;
    return true;
}

bool active()
{
    portENTER_CRITICAL(&mux);
    const bool result = mounted && !storage_fault && enabled && frame_ready;
    portEXIT_CRITICAL(&mux);
    return result;
}

void poll(int64_t now)
{
    static int64_t reported = 0;
    if (now - reported < 2000000) return;
    reported = now;
    portENTER_CRITICAL(&mux);
    const auto style = requested_style;
    const auto minimum = requested_astra_minimum[style];
    const auto classic = requested_astra_minimum[0], linked = requested_astra_minimum[1];
    const auto epoch = astra_epoch, outputs = astra_outputs, blends = astra_blends;
    const auto position = astra_position_milli;
    const auto background = astra_background_milli;
    const auto layers = astra_layers_ready;
    const auto compose_cost = astra_compose_max_us;
    const auto decode_total = astra_decode_total_us, compose_total = astra_compose_total_us;
    const auto read_total = astra_read_total_us, jpeg_total = astra_jpeg_total_us;
    const auto crc_total = astra_crc_total_us;
    const auto rate = astra_rate_milli, cost = astra_blend_max_us;
    const auto id = astra_clip;
    portEXIT_CRITICAL(&mux);
    DeviceLink::printf("@ASTRA_TIMING style=%u min_x10=%u max_x10=%u clip=%u epoch=%lu "
                "source_milli=%llu rate_milli=%lu output=%lu blends=%lu blend_max_us=%lu uptime_ms=%lld "
                "classic_min_x10=%u linked_min_x10=%u bg_milli=%llu bg_rate_milli=1000 layers=%u compose_max_us=%lu "
                "decode_total_us=%llu compose_total_us=%llu read_total_us=%llu jpeg_total_us=%llu crc_total_us=%llu "
                "simd_pixels=%lu frame_exchange=1\n",
                style, minimum, AstraTiming::from_minimum(minimum).maximum, id,
                static_cast<unsigned long>(epoch), static_cast<unsigned long long>(position),
                static_cast<unsigned long>(rate), static_cast<unsigned long>(outputs),
                static_cast<unsigned long>(blends), static_cast<unsigned long>(cost),
                static_cast<long long>(now / 1000), classic, linked,
                static_cast<unsigned long long>(background), unsigned(layers),
                static_cast<unsigned long>(compose_cost), static_cast<unsigned long long>(decode_total),
                static_cast<unsigned long long>(compose_total), static_cast<unsigned long long>(read_total),
                static_cast<unsigned long long>(jpeg_total), static_cast<unsigned long long>(crc_total),
                static_cast<unsigned long>(AstraCompose::verified_pixels()));
}

UploadResult upload(const uint8_t *payload, unsigned size)
{
    return handle_media(payload, size);
}
} // namespace SdMediaPlayer
