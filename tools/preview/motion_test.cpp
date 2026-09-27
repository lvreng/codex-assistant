#include "character_motion.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <string>

namespace {

using Character::Motion;
using Character::Pose;

const Pet::Theme kThemes[] = {Pet::Mechanical, Pet::Paper, Pet::Glass};
const float kDts[] = {.016f, .033f, .10f};
constexpr unsigned kVariantCount = 5;
constexpr unsigned kFaceChannelCount = 12;
constexpr unsigned kTrajectorySamples = 64;
const Character::Channel kFaceChannels[kFaceChannelCount] = {
    Character::EyeL, Character::EyeR, Character::GazeX, Character::GazeY,
    Character::BrowL, Character::BrowR, Character::BrowLift, Character::MouthWidth,
    Character::Smile, Character::Jaw, Character::Pupil, Character::MouthX};

using FaceTrajectory =
    std::array<std::array<std::array<float, kFaceChannelCount>, kTrajectorySamples>,
               kVariantCount>;

[[noreturn]] void fail(const std::string &message)
{
    std::cerr << "FAIL: " << message << '\n';
    std::exit(EXIT_FAILURE);
}

void require(bool condition, const std::string &message)
{
    if (!condition) fail(message);
}

void require_pose(const Pose &pose, const std::string &context)
{
    for (unsigned channel = 0; channel < Character::Count; ++channel) {
        require(std::isfinite(pose[channel]), context + " non-finite channel " +
                                             std::to_string(channel));
        require(std::fabs(pose[channel]) <= 500.0f,
                context + " unbounded channel " + std::to_string(channel));
    }
}

void test_finite_bounded_and_continuous()
{
    for (Pet::Theme theme : kThemes) {
        for (unsigned state_id = 0; state_id < Pet::StateCount; ++state_id) {
            Motion motion;
            motion.reset(theme);
            const auto state = static_cast<Pet::State>(state_id);
            Pose previous = motion.pose();
            for (float dt : kDts) {
                motion.advance(state, dt, .65f, .25f);
                require_pose(motion.pose(), "theme " + std::to_string(theme) +
                                               " state " + std::to_string(state_id));
                previous = motion.pose();
            }

            motion.advance(state, .5f, .65f, 0);
            previous = motion.pose();
            const auto next_state = static_cast<Pet::State>((state_id + 1) % Pet::StateCount);
            motion.advance(next_state, .016f, .65f, 0);
            require_pose(motion.pose(), "state transition");
            float largest_step = 0;
            for (unsigned channel = 0; channel < Character::Count; ++channel)
                largest_step = std::max(largest_step,
                                        std::fabs(motion.pose()[channel] - previous[channel]));
            require(largest_step < 30.0f,
                    "abrupt pose discontinuity, largest step " + std::to_string(largest_step));
        }
    }
}

void test_blinks_are_independent_and_reopen()
{
    Motion motion;
    motion.reset(Pet::Paper);
    bool left_closed = false, right_closed = false, left_reopened = false, right_reopened = false;
    bool eyes_differed = false;
    float left_min = 1, right_min = 1;
    for (int frame = 0; frame < 1800; ++frame) {
        motion.advance(Pet::Idle, .016f, .65f, 0);
        const float left = motion.blink(0);
        const float right = motion.blink(1);
        left_min = std::min(left_min, left);
        right_min = std::min(right_min, right);
        left_closed |= left < .2f;
        right_closed |= right < .2f;
        eyes_differed |= std::fabs(left - right) > .001f;
        if (left_closed && left > .95f) left_reopened = true;
        if (right_closed && right > .95f) right_reopened = true;
    }
    require(left_closed && right_closed, "both eyes did not close during blink cycle");
    require(left_reopened && right_reopened, "both eyes did not reopen after closing");
    require(eyes_differed, "blink eyes are not independently phased");
    require(left_min == 0 && right_min == 0, "blink hold did not fully close both eyes");
}

void test_variants_and_elapsed()
{
    for (Pet::Theme theme : kThemes) {
        for (Pet::State state : {Pet::Thinking, Pet::Done}) {
            Motion motion;
            motion.reset(theme);
            FaceTrajectory trajectories{};
            bool seen[kVariantCount] = {};
            unsigned samples[kVariantCount] = {};
            double previous_elapsed = 0;
            for (int frame = 0; frame < 700; ++frame) {
                motion.advance(state, .10f, .65f, 0);
                require(motion.elapsed() > previous_elapsed,
                        "elapsed time stopped across motif changes");
                previous_elapsed = motion.elapsed();
                const unsigned variant = motion.variant();
                require(variant < kVariantCount, "variant escaped five-ID range");
                seen[variant] = true;
                if (samples[variant] < kTrajectorySamples) {
                    for (unsigned channel = 0; channel < kFaceChannelCount; ++channel)
                        trajectories[variant][samples[variant]][channel] =
                            motion.pose()[kFaceChannels[channel]];
                    ++samples[variant];
                }
            }
            require(motion.elapsed() > 69.0, "elapsed did not retain real state time");
            for (bool variant_seen : seen)
                require(variant_seen, "long-lived state did not visit all five variants");
            for (unsigned variant = 0; variant < kVariantCount; ++variant)
                require(samples[variant] == kTrajectorySamples,
                        "variant did not provide a complete face trajectory");

            for (unsigned first = 0; first < kVariantCount; ++first) {
                for (unsigned second = first + 1; second < kVariantCount; ++second) {
                    float distance = 0;
                    for (unsigned sample = 0; sample < kTrajectorySamples; ++sample) {
                        for (unsigned channel = 0; channel < kFaceChannelCount; ++channel)
                            distance = std::max(
                                distance,
                                std::fabs(trajectories[first][sample][channel] -
                                          trajectories[second][sample][channel]));
                    }
                    require(distance > .25f,
                            "variants " + std::to_string(first) + " and " +
                                std::to_string(second) + " are not face-distinguishable for " +
                                std::to_string(theme) + "/" + std::to_string(state));
                }
            }
        }
    }
}

void test_long_run_and_state_reset()
{
    Motion motion;
    motion.reset(Pet::Paper);
    double previous_elapsed = 0;
    for (int frame = 0; frame < 12000; ++frame) {
        motion.advance(Pet::Thinking, .10f, .65f, 0);
        require(motion.elapsed() > previous_elapsed, "elapsed stopped during long same-state run");
        previous_elapsed = motion.elapsed();
        require_pose(motion.pose(), "long same-state run");
    }
    require(previous_elapsed > 1199.0, "long same-state run lost elapsed time");

    motion.advance(Pet::Done, .016f, .65f, 0);
    require(motion.elapsed() > 0 && motion.elapsed() < .1,
            "real state change did not reset elapsed");
    require(motion.variant() == 0, "real state change did not reset variant");
    require_pose(motion.pose(), "post-reset state");
}

void test_deterministic_replay()
{
    Motion first, second;
    first.reset(Pet::Glass);
    second.reset(Pet::Glass);
    for (int frame = 0; frame < 240; ++frame) {
        const auto state = static_cast<Pet::State>((frame / 40) % Pet::StateCount);
        const float dt = kDts[frame % 3];
        first.advance(state, dt, .8f, .1f);
        second.advance(state, dt, .8f, .1f);
        require(first.variant() == second.variant(), "replay variant diverged");
        require(std::fabs(first.elapsed() - second.elapsed()) < 1e-12,
                "replay elapsed time diverged");
        for (unsigned channel = 0; channel < Character::Count; ++channel)
            require(first.pose()[channel] == second.pose()[channel], "replay pose diverged");
    }
}

} // namespace

int main()
{
    test_finite_bounded_and_continuous();
    test_blinks_are_independent_and_reopen();
    test_variants_and_elapsed();
    test_long_run_and_state_reset();
    test_deterministic_replay();
    std::cout << "motion tests passed: 3 themes, 9 states, face trajectories, long-run elapsed, "
                 "blinks, replay\n";
}
