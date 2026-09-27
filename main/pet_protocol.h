#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>

namespace Pet {

enum State : uint8_t {
    Idle,
    Thinking,
    Working,
    Waiting,
    Done,
    Error,
    Paused,
    Offline,
    Searching,
    StateCount
};

// Append IDs: existing bridge packets retain their meaning.
enum Theme : uint8_t {
    Dark,
    Light,
    Beach,
    Pixel,
    Mechanical,
    Paper,
    Glass,
    VanGogh,
    Adaptive,
    ThemeCount
};

constexpr uint16_t DefaultTransitionMs = 650;
constexpr uint16_t MinTransitionMs = 200;
constexpr uint16_t MaxTransitionMs = 2000;
constexpr unsigned TextWidth = 224;
constexpr unsigned HeadingBytes = TextWidth * 24 / 8;
constexpr unsigned LineBytes = TextWidth * 16 / 8;
constexpr unsigned LabelBytes = 2 * HeadingBytes + 2 * LineBytes;
constexpr unsigned LegacyPayloadBytes = 5 + LabelBytes;
constexpr unsigned ViewPayloadBytes = LegacyPayloadBytes + 7;
constexpr unsigned ModelCount = 4;
constexpr unsigned ModelNameBytes = 20;
constexpr unsigned UsageHeaderBytes = 50;
constexpr unsigned ModelUsageBytes = ModelNameBytes + 4 + 8 + 8 + 4;
constexpr unsigned UsagePayloadBytes =
    ViewPayloadBytes + UsageHeaderBytes + ModelCount * ModelUsageBytes;
constexpr unsigned AccountBytes = 41;
constexpr unsigned AccountPayloadBytes = UsagePayloadBytes + AccountBytes;
constexpr unsigned ExpiryPayloadBytes = AccountPayloadBytes + 17;
constexpr unsigned ArtHeaderBytes = 14;
constexpr unsigned ArtChunkBytes = 2048;
constexpr unsigned ModelPayloadBytes = 2;
constexpr unsigned RotationPayloadBytes = 2;
constexpr unsigned MediaBeginBytes = 12;
constexpr unsigned MediaChunkHeaderBytes = 14;
constexpr unsigned MediaControlBytes = 4;
constexpr unsigned MediaChunkBytes = 16384;
constexpr unsigned MediaMaxPayload = MediaChunkHeaderBytes + MediaChunkBytes;
constexpr unsigned MaxPacket = 8 + std::max(ExpiryPayloadBytes, MediaMaxPayload) + 2;
constexpr uint8_t Magic[] = {0xC0, 0xDE, 0x50, 0x54};

struct ModelUsage {
    char name[ModelNameBytes + 1] = {};
    uint32_t quota = 0;
    uint64_t prompt_tokens = 0;
    uint64_t completion_tokens = 0;
    uint32_t request_count = 0;
};

struct View {
    State state = Offline;
    uint8_t total = 0;
    uint8_t index = 0;
    uint8_t pending = 0;
    uint8_t labels[LabelBytes] = {};
    Theme theme = Dark;
    uint16_t transition_ms = DefaultTransitionMs;
    uint32_t duration_seconds = 0;
    uint32_t balance_quota = UINT32_MAX;
    uint32_t spent_quota = UINT32_MAX;
    uint32_t request_count = UINT32_MAX;
    uint32_t today_request_count = UINT32_MAX;
    uint32_t today_quota = UINT32_MAX;
    uint64_t today_prompt_tokens = UINT64_MAX;
    uint64_t today_completion_tokens = UINT64_MAX;
    uint32_t input_tps_x10 = 0;
    uint32_t output_tps_x10 = 0;
    uint32_t quota_per_unit = 500000;
    uint8_t usage_stale = 1;
    uint8_t model_count = 0;
    ModelUsage models[ModelCount] = {};
    uint8_t api_source = 2;
    uint8_t api_mode = 0;
    uint8_t account_kind = 0;
    uint16_t week_remaining_x10 = UINT16_MAX;
    char week_reset[16] = {};
    char plan_name[20] = {};
    char membership_expires_at[17] = {};
};

inline uint16_t crc16(const uint8_t *data, size_t size)
{
    static constexpr uint16_t table[] = {
        0x0000, 0x1021, 0x2042, 0x3063, 0x4084, 0x50A5, 0x60C6, 0x70E7,
        0x8108, 0x9129, 0xA14A, 0xB16B, 0xC18C, 0xD1AD, 0xE1CE, 0xF1EF,
    };
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < size; ++i) {
        crc = static_cast<uint16_t>((crc << 4) ^ table[(crc >> 12) ^ (data[i] >> 4)]);
        crc = static_cast<uint16_t>((crc << 4) ^ table[(crc >> 12) ^ (data[i] & 15)]);
    }
    return crc;
}

class Receiver {
public:
    bool feed(uint8_t byte)
    {
        if (used_ < 4) {
            if (byte == Magic[used_]) {
                packet_[used_++] = byte;
            } else {
                used_ = byte == Magic[0] ? 1 : 0;
                packet_[0] = Magic[0];
            }
            return false;
        }

        packet_[used_++] = byte;
        if (used_ == 6) {
            length_ = packet_[4] | (packet_[5] << 8);
            if (length_ != 1 && length_ != ModelPayloadBytes &&
                length_ != UsagePayloadBytes && length_ != AccountPayloadBytes && length_ != ExpiryPayloadBytes &&
                length_ != ViewPayloadBytes && length_ != LegacyPayloadBytes &&
                !(length_ >= ArtHeaderBytes && length_ <= ArtHeaderBytes + ArtChunkBytes) &&
                !(length_ >= MediaControlBytes && length_ <= MediaMaxPayload)) {
                used_ = 0;
                ++bad;
                return false;
            }
        }
        if (used_ >= 8 && used_ == 8U + length_ + 2U) {
            used_ = 0;
            const uint16_t expected = packet_[8 + length_] | (packet_[9 + length_] << 8);
            if (expected != crc16(packet_ + 4, 4 + length_)) {
                ++bad;
                return false;
            }
            if (packet_[8] == 3) {
                if (length_ < ArtHeaderBytes || length_ > ArtHeaderBytes + ArtChunkBytes ||
                    packet_[9] > 4) {
                    ++bad;
                    return false;
                }
                ++good;
                return true;
            }
            if (packet_[8] == 4) {
                const uint8_t operation = packet_[9];
                const bool begin_or_end = operation == 1 || operation == 3;
                const bool chunk = operation == 2;
                const bool short_control = operation == 4 || operation == 5;
                const bool valid = (begin_or_end && length_ == MediaBeginBytes) ||
                                   (chunk && length_ >= MediaChunkHeaderBytes &&
                                    length_ <= MediaMaxPayload) ||
                                   (short_control && length_ == MediaControlBytes);
                if (!valid || packet_[10] > 12) {
                    ++bad;
                    return false;
                }
                ++good;
                return true;
            }
            if (packet_[8] == 5) {
                if (length_ != ModelPayloadBytes || packet_[9] > 4) {
                    ++bad;
                    return false;
                }
                ++good;
                return true;
            }
            if (packet_[8] == 6) {
                if (length_ != ModelPayloadBytes || packet_[9] > 1) {
                    ++bad;
                    return false;
                }
                ++good;
                return true;
            }
            if (packet_[8] == 7) {
                if (length_ != 730 || packet_[9] > 7 || packet_[10] > 63 || packet_[11] > 6) {
                    ++bad;
                    return false;
                }
                ++good;
                return true;
            }
            if (packet_[8] == 8) {
                if (length_ != 1) { ++bad; return false; }
                ++good;
                return true;
            }
            if (packet_[8] == 9) {
                if (length_ != RotationPayloadBytes || packet_[9] > 2) {
                    ++bad;
                    return false;
                }
                ++good;
                return true;
            }
            if (packet_[8] == 10) {
                const unsigned minimum = packet_[10] | (packet_[11] << 8);
                if (length_ != 4 || packet_[9] > 1 || minimum < 10 || minimum > 800) {
                    ++bad;
                    return false;
                }
                ++good;
                return true;
            }
            if (length_ != 1 && length_ != UsagePayloadBytes && length_ != AccountPayloadBytes && length_ != ExpiryPayloadBytes &&
                length_ != ViewPayloadBytes && length_ != LegacyPayloadBytes) {
                ++bad;
                return false;
            }
            if ((length_ == 1 && packet_[8] != 2) || (length_ != 1 && packet_[8] != 1)) {
                ++bad;
                return false;
            }
            if (length_ != 1 &&
                (packet_[9] >= StateCount ||
                 (packet_[10] == 0 ? packet_[11] != 0 : packet_[11] >= packet_[10]) ||
                 packet_[12] > packet_[10])) {
                ++bad;
                return false;
            }
            if (length_ == ViewPayloadBytes || length_ == UsagePayloadBytes || has_account()) {
                const unsigned at = 8 + LegacyPayloadBytes;
                const unsigned transition = packet_[at + 1] | (packet_[at + 2] << 8);
                if (packet_[at] >= ThemeCount || transition < MinTransitionMs ||
                    transition > MaxTransitionMs) {
                    ++bad;
                    return false;
                }
            }
            if (has_account()) {
                const unsigned at = 8 + UsagePayloadBytes;
                const unsigned remaining = packet_[at + 3] | (packet_[at + 4] << 8);
                if (packet_[at] < 1 || packet_[at] > 2 || packet_[at + 1] > 2 ||
                    packet_[at + 2] > 2 || (remaining > 1000 && remaining != UINT16_MAX)) {
                    ++bad;
                    return false;
                }
            }
            if (length_ == ExpiryPayloadBytes) {
                const auto *stamp = packet_ + 8 + AccountPayloadBytes;
                bool valid = stamp[16] == 0;
                for (unsigned i = 0; i < 16; ++i) {
                    if (!stamp[0]) valid &= stamp[i] == 0;
                    else if (i == 4 || i == 7) valid &= stamp[i] == '-';
                    else if (i == 10) valid &= stamp[i] == 'T';
                    else if (i == 13) valid &= stamp[i] == ':';
                    else valid &= stamp[i] >= '0' && stamp[i] <= '9';
                }
                if (!valid) { ++bad; return false; }
            }
            ++good;
            return true;
        }
        return false;
    }

    bool is_view() const { return packet_[8] == 1; }
    bool has_account() const { return length_ == AccountPayloadBytes || length_ == ExpiryPayloadBytes; }
    bool is_art() const { return packet_[8] == 3; }
    bool is_media() const { return packet_[8] == 4; }
    bool is_model() const { return packet_[8] == 5; }
    bool is_model_test() const { return packet_[8] == 6; }
    bool is_model_picker() const { return packet_[8] == 7; }
    bool is_disconnect() const { return packet_[8] == 8; }
    bool is_rotation() const { return packet_[8] == 9; }
    bool is_astra_period() const { return packet_[8] == 10; }
    const uint8_t *payload() const { return packet_ + 8; }
    unsigned payload_size() const { return length_; }
    uint8_t model_key() const { return packet_[9]; }
    uint8_t rotation_mode() const { return packet_[9]; }
    uint16_t astra_minimum() const { return packet_[10] | (packet_[11] << 8); }
    bool partial() const { return used_ != 0; }
    void reset_partial()
    {
        if (used_) {
            ++bad;
        }
        used_ = 0;
    }
    uint16_t sequence() const { return packet_[6] | (packet_[7] << 8); }

    void copy_view(View &view) const
    {
        view.state = static_cast<State>(packet_[9]);
        view.total = packet_[10];
        view.index = packet_[11];
        view.pending = packet_[12];
        std::memcpy(view.labels, packet_ + 13, LabelBytes);
        const unsigned at = 8 + LegacyPayloadBytes;
        const bool has_appearance = length_ == ViewPayloadBytes || length_ == UsagePayloadBytes || has_account();
        view.theme = has_appearance ? static_cast<Theme>(packet_[at]) : Dark;
        view.transition_ms = has_appearance
                                 ? static_cast<uint16_t>(packet_[at + 1] | (packet_[at + 2] << 8))
                                 : DefaultTransitionMs;
        view.duration_seconds = has_appearance ? read_u32(at + 3) : 0;
        if (length_ == UsagePayloadBytes || has_account()) {
            const unsigned usage_at = at + 7;
            view.balance_quota = read_u32(usage_at);
            view.spent_quota = read_u32(usage_at + 4);
            view.request_count = read_u32(usage_at + 8);
            view.today_request_count = read_u32(usage_at + 12);
            view.today_quota = read_u32(usage_at + 16);
            view.today_prompt_tokens = read_u64(usage_at + 20);
            view.today_completion_tokens = read_u64(usage_at + 28);
            view.input_tps_x10 = read_u32(usage_at + 36);
            view.output_tps_x10 = read_u32(usage_at + 40);
            view.quota_per_unit = read_u32(usage_at + 44);
            view.usage_stale = packet_[usage_at + 48];
            view.model_count = std::min<unsigned>(packet_[usage_at + 49], ModelCount);
            unsigned model_at = usage_at + UsageHeaderBytes;
            for (unsigned i = 0; i < ModelCount; ++i) {
                std::memcpy(view.models[i].name, packet_ + model_at, ModelNameBytes);
                view.models[i].name[ModelNameBytes] = '\0';
                view.models[i].quota = read_u32(model_at + ModelNameBytes);
                view.models[i].prompt_tokens = read_u64(model_at + ModelNameBytes + 4);
                view.models[i].completion_tokens = read_u64(model_at + ModelNameBytes + 12);
                view.models[i].request_count = read_u32(model_at + ModelNameBytes + 20);
                model_at += ModelUsageBytes;
            }
        } else {
            view.balance_quota = UINT32_MAX;
            view.spent_quota = UINT32_MAX;
            view.request_count = UINT32_MAX;
            view.today_request_count = UINT32_MAX;
            view.today_quota = UINT32_MAX;
            view.today_prompt_tokens = UINT64_MAX;
            view.today_completion_tokens = UINT64_MAX;
            view.input_tps_x10 = 0;
            view.output_tps_x10 = 0;
            view.quota_per_unit = 500000;
            view.usage_stale = 1;
            view.model_count = 0;
            view.duration_seconds = 0;
            for (ModelUsage &model : view.models) {
                model = ModelUsage{};
            }
        }
        view.api_source = 2;
        view.api_mode = view.account_kind = 0;
        view.week_remaining_x10 = UINT16_MAX;
        view.week_reset[0] = view.plan_name[0] = '\0';
        if (has_account()) {
            const unsigned account_at = 8 + UsagePayloadBytes;
            view.api_source = packet_[account_at];
            view.api_mode = packet_[account_at + 1];
            view.account_kind = packet_[account_at + 2];
            view.week_remaining_x10 = packet_[account_at + 3] | (packet_[account_at + 4] << 8);
            std::memcpy(view.week_reset, packet_ + account_at + 5, 15);
            std::memcpy(view.plan_name, packet_ + account_at + 21, 19);
            view.week_reset[15] = view.plan_name[19] = '\0';
        }
        std::memset(view.membership_expires_at, 0, sizeof(view.membership_expires_at));
        if (length_ == ExpiryPayloadBytes) {
            std::memcpy(view.membership_expires_at, packet_ + 8 + AccountPayloadBytes, 17);
        }
    }

    uint32_t good = 0;
    uint32_t bad = 0;

private:
    uint32_t read_u32(unsigned at) const
    {
        return static_cast<uint32_t>(packet_[at]) |
               (static_cast<uint32_t>(packet_[at + 1]) << 8) |
               (static_cast<uint32_t>(packet_[at + 2]) << 16) |
               (static_cast<uint32_t>(packet_[at + 3]) << 24);
    }

    uint64_t read_u64(unsigned at) const
    {
        uint64_t value = 0;
        for (unsigned i = 0; i < 8; ++i) {
            value |= static_cast<uint64_t>(packet_[at + i]) << (i * 8);
        }
        return value;
    }

    uint8_t packet_[MaxPacket] = {};
    unsigned used_ = 0;
    unsigned length_ = 0;
};

} // namespace Pet
