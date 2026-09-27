#pragma once

#include <cstdint>

namespace ModelButton {

enum class Event { None, BeginHold, CancelHold, Theme, Open, Next, Confirm };

class Controller {
public:
    Event update(bool raw_pressed, uint32_t now_ms, bool sidebar_open)
    {
        last_now_ = now_ms;
        if (!initialized_) {
            initialized_ = true;
            raw_state_ = raw_pressed;
            raw_changed_at_ = now_ms;
            return Event::None;
        }

        Event event = Event::None;
        if (raw_pressed != raw_state_) {
            raw_state_ = raw_pressed;
            raw_changed_at_ = now_ms;
            if (raw_state_ && !stable_pressed_ && !sidebar_open && !hold_announced_) {
                context_at_press_ = false;
                hold_announced_ = true;
                event = Event::BeginHold;
            }
        }

        if (raw_state_ != stable_pressed_ && elapsed(now_ms, raw_changed_at_) >= DebounceMs) {
            stable_pressed_ = raw_state_;
            if (stable_pressed_) {
                press_started_at_ = now_ms;
                context_at_press_ = sidebar_open;
                fired_ = false;
                if (!context_at_press_ && !hold_announced_) {
                    hold_announced_ = true;
                    event = Event::BeginHold;
                }
            } else {
                if (!fired_)
                    event = context_at_press_ ? Event::Next : Event::Theme;
                fired_ = false;
                hold_announced_ = false;
            }
        } else if (hold_announced_ && !raw_state_ && !stable_pressed_ &&
                   elapsed(now_ms, raw_changed_at_) >= DebounceMs) {
            hold_announced_ = false;
            event = Event::CancelHold;
        }

        if (stable_pressed_ && raw_state_ && !fired_ && elapsed(now_ms, press_started_at_) >= HoldMs) {
            fired_ = true;
            event = context_at_press_ ? Event::Confirm : Event::Open;
        }
        return event;
    }

    unsigned progress() const
    {
        if (!stable_pressed_ || fired_)
            return 0;
        const uint32_t elapsed_ms = elapsed(last_now_, press_started_at_);
        if (elapsed_ms <= ProgressStartMs)
            return 0;
        const uint32_t active_ms = elapsed_ms - ProgressStartMs;
        const uint32_t range_ms = HoldMs - ProgressStartMs;
        const uint32_t value = (active_ms * 1000u) / range_ms;
        return value > 1000u ? 1000u : static_cast<unsigned>(value);
    }

    bool holding() const { return stable_pressed_; }

private:
    static constexpr uint32_t DebounceMs = 30;
    static constexpr uint32_t ProgressStartMs = 100;
    static constexpr uint32_t HoldMs = 900;

    static uint32_t elapsed(uint32_t now, uint32_t then) { return now - then; }

    bool initialized_ = false;
    bool raw_state_ = false;
    bool stable_pressed_ = false;
    bool context_at_press_ = false;
    bool fired_ = false;
    bool hold_announced_ = false;
    uint32_t raw_changed_at_ = 0;
    uint32_t press_started_at_ = 0;
    uint32_t last_now_ = 0;
};

} // namespace ModelButton
