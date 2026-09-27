#pragma once
#include <cstdint>

namespace DeviceLink {
enum class Source : uint8_t { Usb, Bluetooth };
struct Policy {
    static constexpr int64_t UsbLeaseUs = 2500000;
    int64_t last_usb = -UsbLeaseUs;
    Source active = Source::Usb;

    bool accept(Source source, int64_t now) {
        if (source == Source::Usb) {
            last_usb = now;
            active = source;
            return true;
        }
        if (now - last_usb < UsbLeaseUs) return false;
        active = source;
        return true;
    }
};
} // namespace DeviceLink
