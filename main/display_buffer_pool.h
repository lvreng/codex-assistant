#ifndef DISPLAY_BUFFER_POOL_H
#define DISPLAY_BUFFER_POOL_H

#include <assert.h>
#include <stdint.h>

namespace DisplayBuffers {

class Pool {
 public:
  Pool() : latest_(0) {
    for (int i = 0; i < 3; ++i) {
      valid_[i] = true;
      retired_[i] = false;
      deadlines_[i] = 0;
    }
  }

  int find_free(uint32_t frame_events) {
    for (int i = 0; i < 3; ++i) {
      if (i == latest_ || !valid_[i]) {
        continue;
      }
      if (!retired_[i] || reached(frame_events, deadlines_[i])) {
        retired_[i] = false;
        return i;
      }
    }
    return -1;
  }

  void submitted(int buffer, uint32_t frame_events_after_submission) {
    assert(buffer >= 0 && buffer < 3);
    assert(valid_[buffer]);
    assert(buffer != latest_);
    assert(!retired_[buffer]);

    retired_[latest_] = true;
    deadlines_[latest_] = frame_events_after_submission + 2u;
    latest_ = buffer;
    retired_[latest_] = false;
  }

  int latest() const { return latest_; }

 private:
  static bool reached(uint32_t now, uint32_t deadline) {
    return static_cast<int32_t>(now - deadline) >= 0;
  }

  bool valid_[3];
  bool retired_[3];
  uint32_t deadlines_[3];
  int latest_;
};

}  // namespace DisplayBuffers

#endif
