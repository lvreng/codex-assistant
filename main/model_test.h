#pragma once
#include <cstdint>

namespace Thinker {
class ModelTest {
public:
    static constexpr int64_t StepUs = 6000000;
    void start(int64_t now) { if (!model(now)) started_ = now; }
    void stop() { started_ = -1; }
    uint8_t model(int64_t now) const
    {
        if (started_ < 0 || now < started_ || now-started_ >= StepUs*4) return 0;
        return uint8_t((now-started_)/StepUs+1);
    }
private:
    int64_t started_ = -1;
};
}
