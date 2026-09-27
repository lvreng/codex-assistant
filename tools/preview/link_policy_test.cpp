#include "link_policy.h"
#include <cassert>

int main() {
    using namespace DeviceLink;
    Policy policy;
    assert(policy.accept(Source::Bluetooth, 0));
    assert(policy.active == Source::Bluetooth);
    assert(policy.accept(Source::Usb, 1));
    assert(!policy.accept(Source::Bluetooth, 2500000));
    assert(policy.active == Source::Usb);
    assert(policy.accept(Source::Bluetooth, 2500001));
    assert(policy.accept(Source::Usb, 2500002));
    for (int64_t now = 3000000; now < 6000000; now += 100000) {
        assert(policy.accept(Source::Usb, now));
        assert(!policy.accept(Source::Bluetooth, now+99999));
    }
}
