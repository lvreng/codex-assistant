#include "display_damage.h"
#include <array>
#include <cassert>
#include <vector>

int main()
{
    using namespace DisplayDamage;
    Tracker tracker;
    constexpr int W = 80, H = 48;
    tracker.reset(W, H);
    std::vector<unsigned> canvas(W*H);
    std::array<std::vector<unsigned>, 3> buffers;
    for (auto &buffer : buffers) buffer.resize(W*H);
    unsigned random = 73;
    auto next = [&]() { random = random*1664525U+1013904223U; return random >> 16; };
    for (unsigned frame = 1; frame <= 10000; ++frame) {
        // Non-round-robin reuse and dropped submissions exercise stale buffers.
        Area changed{int(next()%W), int(next()%H), int(next()%W), int(next()%H)};
        if (changed.x1 > changed.x2) std::swap(changed.x1, changed.x2);
        if (changed.y1 > changed.y2) std::swap(changed.y1, changed.y2);
        for (int y = changed.y1; y <= changed.y2; ++y)
            for (int x = changed.x1; x <= changed.x2; ++x) canvas[y*W+x] = frame;
        tracker.invalidate(changed);
        if (frame%7 == 0) continue;
        const unsigned back = next()%3;
        const auto area = tracker.pending(back);
        const auto rotated = tracker.rotated(area);
        assert(rotated.width() == area.height() && rotated.height() == area.width());
        for (int y = area.y1; y <= area.y2; ++y)
            for (int x = area.x1; x <= area.x2; ++x)
                buffers[back][(W-1-x)*H+y] = canvas[y*W+x];
        for (int y = 0; y < H; ++y)
            for (int x = 0; x < W; ++x)
                assert(buffers[back][(W-1-x)*H+y] == canvas[y*W+x]);
        tracker.completed(back);
        assert(tracker.pending(back).empty());
    }
    for (int i = 0; i < 3; ++i) tracker.completed(i);
    tracker.invalidate({-20, -5, -1, 20});
    assert(tracker.pending(0).empty());
    tracker.invalidate({-20, -5, 0, 20});
    assert(tracker.pending(1).pixels() == 21);
    tracker.invalidate({W-1, H-1, W+50, H+50});
    assert(tracker.pending(2).pixels() == W*H);
}
