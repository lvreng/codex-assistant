#include "device_link.h"
#include "driver/usb_serial_jtag.h"
#include "esp_hosted.h"
#include "esp_log.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/stream_buffer.h"
#include "freertos/task.h"
#include "host/ble_hs.h"
#include "host/ble_store.h"
#include "host/util/util.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"
#include <algorithm>
#include <atomic>
#include <cstdarg>
#include <cstdio>
#include <cstring>

extern "C" void ble_store_config_init(void);

namespace DeviceLink {
namespace {
constexpr char Tag[] = "pet_ble";
constexpr size_t QueueBytes = 32768;
StreamBufferHandle_t receive_queue = nullptr;
SemaphoreHandle_t transmit_lock = nullptr;
TaskHandle_t transmit_task = nullptr;
uint8_t *transmit_bytes = nullptr;
uint8_t *receive_bytes = nullptr;
StaticStreamBuffer_t receive_control;
size_t tx_read = 0, tx_write = 0, tx_size = 0;
std::atomic<uint16_t> connection{BLE_HS_CONN_HANDLE_NONE};
std::atomic<bool> subscribed{false}, encrypted{false}, ready{false};
std::atomic<int64_t> last_usb{-5000000};
std::atomic<uint32_t> generation{0};
std::atomic<int> startup_stage{0}, startup_error{0};
uint32_t firmware_version[3]{};
Policy policy;
uint16_t notify_handle = 0;
uint8_t address_type = 0;
char address[18] = {};
// Nordic UART UUIDs, little-endian as required by NimBLE.
ble_uuid128_t service_uuid = BLE_UUID128_INIT(0x9e,0xca,0xdc,0x24,0x0e,0xe5,0xa9,0xe0,0x93,0xf3,0xa3,0xb5,1,0,0x40,0x6e);
ble_uuid128_t write_uuid = BLE_UUID128_INIT(0x9e,0xca,0xdc,0x24,0x0e,0xe5,0xa9,0xe0,0x93,0xf3,0xa3,0xb5,2,0,0x40,0x6e);
ble_uuid128_t notify_uuid = BLE_UUID128_INIT(0x9e,0xca,0xdc,0x24,0x0e,0xe5,0xa9,0xe0,0x93,0xf3,0xa3,0xb5,3,0,0x40,0x6e);
ble_gatt_chr_def characteristics[3]{};
ble_gatt_svc_def services[2]{};

void clear_queues() {
    // RX is drained by the protocol task; its generation change resets the parser.
    generation.fetch_add(1);
    if (transmit_lock && xSemaphoreTake(transmit_lock, pdMS_TO_TICKS(10))) {
        tx_read = tx_write = tx_size = 0;
        xSemaphoreGive(transmit_lock);
    }
}

int access(uint16_t, uint16_t, ble_gatt_access_ctxt *ctxt, void *) {
    if (ctxt->op != BLE_GATT_ACCESS_OP_WRITE_CHR) return BLE_ATT_ERR_READ_NOT_PERMITTED;
    const size_t length = OS_MBUF_PKTLEN(ctxt->om);
    uint8_t bytes[512];
    if (!encrypted.load()) return BLE_ATT_ERR_INSUFFICIENT_ENC;
    if (length > sizeof(bytes) || xStreamBufferSpacesAvailable(receive_queue) < length)
        return BLE_ATT_ERR_INSUFFICIENT_RES;
    if (ble_hs_mbuf_to_flat(ctxt->om, bytes, sizeof(bytes), nullptr)) return BLE_ATT_ERR_UNLIKELY;
    return xStreamBufferSend(receive_queue, bytes, length, 0) == length ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
}

void advertise();

int gap_event(ble_gap_event *event, void *) {
    switch (event->type) {
    case BLE_GAP_EVENT_CONNECT: {
        printf("@BLE_EVENT connect status=%d\n", event->connect.status);
        if (event->connect.status) { advertise(); break; }
        ble_gap_conn_desc desc{};
        if (ble_gap_conn_find(event->connect.conn_handle, &desc)) {
            ble_gap_terminate(event->connect.conn_handle, BLE_ERR_REM_USER_CONN_TERM);
            break;
        }
        ble_addr_t peers[1]{};
        int count = 0;
        const int result = ble_store_util_bonded_peers(peers, &count, 1);
        const bool known = result == 0 && count == 1 && ble_addr_cmp(&peers[0], &desc.peer_id_addr) == 0;
        printf("@BLE_EVENT peer stored=%d count=%d known=%u usb_age_ms=%lld\n", result, count,
               known, (long long)((esp_timer_get_time()-last_usb.load())/1000));
        // First pairing is possible only while the wired bridge is present.
        if (!known && (result != 0 || count != 0 || esp_timer_get_time()-last_usb.load() > 5000000)) {
            ble_gap_terminate(event->connect.conn_handle, BLE_ERR_REM_USER_CONN_TERM);
            break;
        }
        clear_queues();
        connection.store(event->connect.conn_handle);
        ble_gap_upd_params parameters{};
        parameters.itvl_min = 6;
        parameters.itvl_max = 12;
        parameters.latency = 0;
        parameters.supervision_timeout = 400;
        ble_gap_update_params(event->connect.conn_handle, &parameters);
        break;
    }
    case BLE_GAP_EVENT_DISCONNECT:
        printf("@BLE_EVENT disconnect reason=%d\n", event->disconnect.reason);
        connection.store(BLE_HS_CONN_HANDLE_NONE);
        subscribed.store(false);
        encrypted.store(false);
        clear_queues();
        advertise();
        break;
    case BLE_GAP_EVENT_ENC_CHANGE: {
        printf("@BLE_EVENT encryption status=%d\n", event->enc_change.status);
        ble_gap_conn_desc desc{};
        if (!event->enc_change.status && !ble_gap_conn_find(event->enc_change.conn_handle, &desc))
            encrypted.store(desc.sec_state.encrypted);
        break;
    }
    case BLE_GAP_EVENT_SUBSCRIBE:
        if (event->subscribe.attr_handle == notify_handle) subscribed.store(event->subscribe.cur_notify);
        break;
    case BLE_GAP_EVENT_MTU:
        printf("@BLE_EVENT mtu=%u\n", event->mtu.value);
        break;
    case BLE_GAP_EVENT_CONN_UPDATE: {
        ble_gap_conn_desc desc{};
        if (!ble_gap_conn_find(event->conn_update.conn_handle, &desc))
            printf("@BLE_EVENT interval_x1250us=%u status=%d\n", desc.conn_itvl,
                   event->conn_update.status);
        break;
    }
    case BLE_GAP_EVENT_NOTIFY_TX:
        if (transmit_task) xTaskNotifyGive(transmit_task);
        break;
    case BLE_GAP_EVENT_REPEAT_PAIRING:
        printf("@BLE_EVENT repeat_pairing rejected=1\n");
        // Never silently delete the bonded computer to admit an unknown client.
        return BLE_GAP_REPEAT_PAIRING_IGNORE;
    case BLE_GAP_EVENT_ADV_COMPLETE:
        advertise();
        break;
    default: break;
    }
    return 0;
}

void advertise() {
    ble_hs_adv_fields fields{};
    fields.flags = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP;
    fields.uuids128 = &service_uuid;
    fields.num_uuids128 = 1;
    fields.uuids128_is_complete = 1;
    int result = ble_gap_adv_set_fields(&fields);
    ble_hs_adv_fields response{};
    const char *device_name = ble_svc_gap_device_name();
    response.name = reinterpret_cast<const uint8_t *>(device_name);
    response.name_len = std::strlen(device_name);
    response.name_is_complete = 1;
    if (!result) result = ble_gap_adv_rsp_set_fields(&response);
    ble_gap_adv_params params{};
    params.conn_mode = BLE_GAP_CONN_MODE_UND;
    params.disc_mode = BLE_GAP_DISC_MODE_GEN;
    params.itvl_min = 160;
    params.itvl_max = 240;
    if (!result) result = ble_gap_adv_start(address_type, nullptr, BLE_HS_FOREVER, &params, gap_event, nullptr);
    if (result) ESP_LOGW(Tag, "advertising failed: %d", result);
}

void synced() {
    int result = ble_hs_util_ensure_addr(0);
    if (!result) result = ble_hs_id_infer_auto(0, &address_type);
    if (result) { startup_error.store(result); return; }
    uint8_t bytes[6];
    if (ble_hs_id_copy_addr(address_type, bytes, nullptr)) return;
    std::snprintf(address, sizeof(address), "%02X:%02X:%02X:%02X:%02X:%02X",
                  bytes[5], bytes[4], bytes[3], bytes[2], bytes[1], bytes[0]);
    ready.store(true);
    startup_stage.store(6);
    advertise();
}

void host_task(void *) {
    nimble_port_run();
    nimble_port_freertos_deinit();
}

void sender(void *) {
    uint8_t bytes[244];
    uint32_t previous_generation = generation.load();
    size_t pending = 0;
    for (;;) {
        const uint32_t epoch = generation.load();
        if (epoch != previous_generation) { pending = 0; previous_generation = epoch; }
        const uint16_t handle = connection.load();
        if (handle != BLE_HS_CONN_HANDLE_NONE && subscribed.load() && encrypted.load()) {
            if (!pending && xSemaphoreTake(transmit_lock, pdMS_TO_TICKS(2))) {
                pending = std::min({tx_size, sizeof(bytes), size_t(std::max(23, int(ble_att_mtu(handle)))-3)});
                for (size_t i = 0; i < pending; ++i) { bytes[i] = transmit_bytes[tx_read]; tx_read = (tx_read+1)%QueueBytes; }
                tx_size -= pending;
                xSemaphoreGive(transmit_lock);
            }
            if (pending) {
                os_mbuf *packet = ble_hs_mbuf_from_flat(bytes, pending);
                if (packet && ble_gatts_notify_custom(handle, notify_handle, packet) == 0) pending = 0;
            }
        }
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(pending ? 5 : 10));
    }
}

void start(void *) {
    // Display/SD initialization has finished before this task is created.
    startup_stage.store(2);
    const int transport_result = esp_hosted_connect_to_slave();
    if (transport_result != ESP_OK) {
        startup_error.store(transport_result);
        ESP_LOGW(Tag, "C6 unavailable; USB remains active");
        vTaskDelete(nullptr);
        return;
    }
    esp_hosted_coprocessor_fwver_t version{};
    startup_stage.store(3);
    const bool version_known = esp_hosted_get_coprocessor_fwversion(&version) == ESP_OK;
    firmware_version[0] = version.major1;
    firmware_version[1] = version.minor1;
    firmware_version[2] = version.patch1;
    ESP_LOGI(Tag, "C6 firmware %lu.%lu.%lu, version_known=%u", (unsigned long)version.major1,
             (unsigned long)version.minor1, (unsigned long)version.patch1, version_known);
    // Pre-2.5.2 C6 firmware enables its controller at boot and has no lifecycle RPCs.
    if (version_known && (version.major1 > 2 || (version.major1 == 2 &&
        (version.minor1 > 5 || (version.minor1 == 5 && version.patch1 >= 2))))) {
        if (esp_hosted_bt_controller_init() != ESP_OK || esp_hosted_bt_controller_enable() != ESP_OK) {
            startup_error.store(-2);
            ESP_LOGW(Tag, "C6 Bluetooth controller unavailable; keeping USB");
            vTaskDelete(nullptr);
            return;
        }
    }
    startup_stage.store(4);
    const int result = nimble_port_init();
    if (result != ESP_OK) { startup_error.store(result); ESP_LOGW(Tag, "NimBLE init failed: %d", result); vTaskDelete(nullptr); return; }
    ble_hs_cfg.sync_cb = synced;
    ble_hs_cfg.sm_io_cap = BLE_HS_IO_NO_INPUT_OUTPUT;
    ble_hs_cfg.sm_bonding = 1;
    ble_hs_cfg.sm_sc = 1;
    ble_hs_cfg.sm_our_key_dist = BLE_SM_PAIR_KEY_DIST_ENC | BLE_SM_PAIR_KEY_DIST_ID;
    ble_hs_cfg.sm_their_key_dist = BLE_SM_PAIR_KEY_DIST_ENC | BLE_SM_PAIR_KEY_DIST_ID;
    ble_svc_gap_init();
    ble_svc_gatt_init();
    ble_svc_gap_device_name_set("Codex-P4");
    characteristics[0].uuid = &write_uuid.u;
    characteristics[0].access_cb = access;
    characteristics[0].flags = BLE_GATT_CHR_F_WRITE | BLE_GATT_CHR_F_WRITE_NO_RSP | BLE_GATT_CHR_F_WRITE_ENC;
    characteristics[1].uuid = &notify_uuid.u;
    characteristics[1].access_cb = access;
    characteristics[1].flags = BLE_GATT_CHR_F_NOTIFY;
    characteristics[1].val_handle = &notify_handle;
    services[0].type = BLE_GATT_SVC_TYPE_PRIMARY;
    services[0].uuid = &service_uuid.u;
    services[0].characteristics = characteristics;
    if (ble_gatts_count_cfg(services) || ble_gatts_add_svcs(services)) {
        startup_error.store(-3);
        ESP_LOGW(Tag, "GATT registration failed"); vTaskDelete(nullptr); return;
    }
    ble_store_config_init();
    startup_stage.store(5);
    nimble_port_freertos_init(host_task);
    if (xTaskCreate(sender, "pet_ble_tx", 4096, nullptr, 3, &transmit_task) != pdPASS) {
        startup_error.store(-6);
        ESP_LOGW(Tag, "BLE sender unavailable");
    }
    vTaskDelete(nullptr);
}
} // namespace

void init() {
    // Per-notification INFO logs use blocking stdout, which can stall HCI when
    // no computer is draining USB. Our link diagnostics are non-blocking.
    esp_log_level_set("NimBLE", ESP_LOG_WARN);
    // FreeRTOS's dynamic stream buffers use internal RAM; these byte queues do
    // not run in an ISR and belong in PSRAM alongside the full-size UI assets.
    receive_bytes = static_cast<uint8_t *>(heap_caps_malloc(QueueBytes+1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    transmit_bytes = static_cast<uint8_t *>(heap_caps_malloc(QueueBytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (receive_bytes) receive_queue = xStreamBufferCreateStatic(QueueBytes+1, 1, receive_bytes, &receive_control);
    transmit_lock = xSemaphoreCreateMutex();
    if (!receive_queue || !transmit_bytes || !transmit_lock) { startup_error.store(-4); ESP_LOGW(Tag, "BLE queues unavailable"); return; }
    startup_stage.store(1);
    if (xTaskCreate(start, "pet_ble_init", 6144, nullptr, 2, nullptr) != pdPASS) startup_error.store(-5);
}

int read_bluetooth(uint8_t *buffer, size_t capacity) {
    static uint32_t previous = 0;
    const uint32_t epoch = generation.load();
    if (previous != epoch) {
        previous = epoch;
        while (receive_queue && xStreamBufferReceive(receive_queue, buffer, capacity, 0)) {}
        return -1;
    }
    return receive_queue ? xStreamBufferReceive(receive_queue, buffer, capacity, 0) : 0;
}

bool accept(Source source, int64_t now) {
    if (source == Source::Usb) last_usb.store(now);
    return policy.accept(source, now);
}

const char *name() { return policy.active == Source::Usb ? "usb" : "ble"; }

void hello() {
    printf("@BLE_STATUS stage=%d error=%d firmware=%lu.%lu.%lu\n", startup_stage.load(), startup_error.load(),
           (unsigned long)firmware_version[0], (unsigned long)firmware_version[1], (unsigned long)firmware_version[2]);
    if (ready.load()) {
        const auto handle = connection.load();
        printf("@BLE_ID %s connected=%u encrypted=%u mtu=%u\n", address,
               handle != BLE_HS_CONN_HANDLE_NONE, encrypted.load(),
               handle == BLE_HS_CONN_HANDLE_NONE ? 0 : ble_att_mtu(handle));
    }
}

int printf(const char *format, ...) {
    char line[1400];
    va_list args;
    va_start(args, format);
    const int size = std::vsnprintf(line, sizeof(line), format, args);
    va_end(args);
    if (size <= 0 || size >= int(sizeof(line))) return -1;
    // Diagnostics must never stall rendering when the USB cable is absent.
    usb_serial_jtag_write_bytes(line, size, 0);
    if (!subscribed.load() || !encrypted.load() || !transmit_lock) return size;
    if (!xSemaphoreTake(transmit_lock, pdMS_TO_TICKS(5))) {
        const auto handle = connection.load();
        if (handle != BLE_HS_CONN_HANDLE_NONE) ble_gap_terminate(handle, BLE_ERR_REM_USER_CONN_TERM);
        return size;
    }
    if (tx_size + size <= QueueBytes) {
        for (int i = 0; i < size; ++i) { transmit_bytes[tx_write] = line[i]; tx_write = (tx_write+1)%QueueBytes; }
        tx_size += size;
    } else {
        // Do not splice a truncated notification stream into a valid session.
        const auto handle = connection.load();
        xSemaphoreGive(transmit_lock);
        if (handle != BLE_HS_CONN_HANDLE_NONE) ble_gap_terminate(handle, BLE_ERR_REM_USER_CONN_TERM);
        return size;
    }
    xSemaphoreGive(transmit_lock);
    if (transmit_task) xTaskNotifyGive(transmit_task);
    return size;
}
} // namespace DeviceLink
