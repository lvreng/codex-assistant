#pragma once

#include <algorithm>
#include <array>

namespace DisplayDamage {
struct Area {
    int x1 = 0, y1 = 0, x2 = -1, y2 = -1;
    bool empty() const { return x2 < x1 || y2 < y1; }
    int width() const { return empty() ? 0 : x2-x1+1; }
    int height() const { return empty() ? 0 : y2-y1+1; }
    int pixels() const { return width()*height(); }

    void include(Area other)
    {
        if (other.empty()) return;
        if (empty()) { *this = other; return; }
        x1 = std::min(x1, other.x1);
        y1 = std::min(y1, other.y1);
        x2 = std::max(x2, other.x2);
        y2 = std::max(y2, other.y2);
    }
};

// Each back buffer keeps damage until it has received those pixels, including
// changes from frames it never presented. Otherwise old UI pixels can return.
class Tracker {
public:
    void reset(int width, int height)
    {
        full_ = {0, 0, width-1, height-1};
        pending_.fill(full_);
    }
    void invalidate(Area area)
    {
        area.x1 = std::max(full_.x1, area.x1);
        area.y1 = std::max(full_.y1, area.y1);
        area.x2 = std::min(full_.x2, area.x2);
        area.y2 = std::min(full_.y2, area.y2);
        for (auto &pending : pending_) pending.include(area);
    }
    Area pending(unsigned buffer) const { return pending_.at(buffer); }
    void completed(unsigned buffer) { pending_.at(buffer) = {}; }
    Area full() const { return full_; }
    Area rotated(Area area) const
    {
        if (area.empty()) return {};
        return {area.y1, full_.x2-area.x2, area.y2, full_.x2-area.x1};
    }
private:
    Area full_;
    std::array<Area, 3> pending_;
};
}
