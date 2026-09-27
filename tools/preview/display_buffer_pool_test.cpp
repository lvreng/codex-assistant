#include "../../main/display_buffer_pool.h"

#include <assert.h>
#include <stdint.h>

namespace {

struct Reference {
  Reference() : latest(0) {
    for (int i = 0; i < 3; ++i) {
      retired[i] = false;
      deadlines[i] = 0;
    }
  }

  int find_free(uint64_t event) {
    for (int i = 0; i < 3; ++i) {
      if (i == latest) continue;
      if (!retired[i] || event >= deadlines[i]) {
        retired[i] = false;
        return i;
      }
    }
    return -1;
  }

  void submitted(int buffer, uint64_t event) {
    retired[latest] = true;
    deadlines[latest] = event + 2u;
    latest = buffer;
    retired[latest] = false;
  }

  bool retired[3];
  uint64_t deadlines[3];
  int latest;
};

void basic_cases() {
  DisplayBuffers::Pool pool;
  assert(pool.latest() == 0);
  assert(pool.find_free(0) == 1);
  pool.submitted(1, 0);
  assert(pool.latest() == 1);
  assert(pool.find_free(0) == 2);
  pool.submitted(2, 0);
  assert(pool.find_free(0) == -1);
  assert(pool.find_free(1) == -1);
  assert(pool.find_free(100) == 0);
  pool.submitted(0, 100);
  assert(pool.find_free(100) == 1);
  pool.submitted(1, 100);
  assert(pool.find_free(100) == -1);
  assert(pool.find_free(102) == 0);
}

void wrap_case() {
  DisplayBuffers::Pool pool;
  const uint32_t event = 0xfffffffeu;
  assert(pool.find_free(event) == 1);
  pool.submitted(1, event);
  assert(pool.find_free(0xffffffffu) == 2);
  pool.submitted(2, 0xffffffffu);
  assert(pool.find_free(0u) == 0);
  assert(pool.find_free(1u) == 0);
}

void randomized_case() {
  DisplayBuffers::Pool pool;
  Reference reference;
  uint64_t event = 0xfffff000u;
  uint32_t state = 0x12345678u;
  for (int step = 0; step < 10000; ++step) {
    state = state * 1664525u + 1013904223u;
    event += (state >> 29) & 3u;
    int actual = pool.find_free(event);
    int expected = reference.find_free(event);
    assert(actual == expected);
    if (actual >= 0) {
      pool.submitted(actual, event);
      reference.submitted(expected, event);
    }
    assert(pool.latest() == reference.latest);
    assert(pool.find_free(event) == reference.find_free(event));
  }
}

}  // namespace

int main() {
  basic_cases();
  wrap_case();
  randomized_case();
  return 0;
}
