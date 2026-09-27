#pragma once
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include "link_policy.h"

namespace DeviceLink {
#ifdef PET_DESKTOP_PREVIEW
using std::printf;
#else
void init();
int read_bluetooth(uint8_t *buffer, size_t capacity);
bool accept(Source source, int64_t now);
void hello();
const char *name();
int printf(const char *format, ...) __attribute__((format(printf, 1, 2)));
#endif
} // namespace DeviceLink
