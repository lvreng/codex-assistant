#pragma once

#include <array>
#include "pet_protocol.h"

namespace Character {
enum Channel {
    X, Y, Roll, Stretch, EyeL, EyeR, GazeX, GazeY, Pupil,
    BrowL, BrowR, BrowLift, MouthWidth, Smile, Jaw,
    HandLX, HandLY, HandRX, HandRY, Happy, Alarm, Sleep, Focus,
    BodyRoll, BodyY, MouthX, Shine, Celebrate, Count
};
using Pose = std::array<float, Count>;

class Motion {
public:
    void reset(Pet::Theme theme);
    void advance(Pet::State state, float dt, float transition_seconds, float touch);
    const Pose &pose() const { return pose_; }
    float blink(unsigned eye) const;
    unsigned variant() const { return variant_; }
    double elapsed() const { return state_time_; }

private:
    Pose pose_{}, velocity_{};
    Pet::Theme theme_ = Pet::Mechanical;
    Pet::State state_ = Pet::Offline;
    double clock_ = 0, state_time_ = 0;
    float phrase_time_ = 0, phrase_duration_ = 9, blink_age_ = 1, next_blink_ = 2.8f;
    unsigned variant_ = 0;
    uint32_t random_ = 0xB65D13u;
    bool initialized_ = false;
    float random_unit();
    Pose target() const;
};
} // namespace Character
