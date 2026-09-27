#include "model_button.h"

#include <cassert>
#include <cstdint>

using ModelButton::Controller;
using ModelButton::Event;

static void settle(Controller &button, bool pressed, uint32_t at, bool sidebar)
{
    assert(button.update(pressed, at, sidebar) == Event::None);
    assert(button.update(pressed, at + 30, sidebar) ==
           (sidebar ? Event::None : Event::BeginHold));
}

int main()
{
    Controller bounce;
    assert(bounce.update(false, 0, false) == Event::None);
    assert(bounce.update(true, 5, false) == Event::BeginHold);
    assert(bounce.update(false, 10, false) == Event::None);
    assert(bounce.update(true, 20, false) == Event::None);
    assert(bounce.update(true, 49, false) == Event::None);
    assert(!bounce.holding());
    assert(bounce.update(true, 50, false) == Event::None);
    assert(bounce.holding());
    assert(bounce.update(false, 55, false) == Event::None);
    assert(bounce.update(false, 84, false) == Event::None);
    assert(bounce.update(false, 85, false) == Event::Theme);

    Controller glitch;
    assert(glitch.update(false, 0, false) == Event::None);
    assert(glitch.update(true, 5, false) == Event::BeginHold);
    assert(glitch.update(false, 10, false) == Event::None);
    assert(glitch.update(false, 40, false) == Event::CancelHold);
    assert(!glitch.holding());

    Controller short_press;
    settle(short_press, true, 100, false);
    assert(short_press.progress() == 0);
    assert(short_press.update(false, 300, false) == Event::None);
    assert(short_press.update(false, 330, false) == Event::Theme);

    Controller open;
    settle(open, true, 1000, false);
    assert(open.update(true, 1099, false) == Event::None);
    assert(open.progress() == 0);
    assert(open.update(true, 1140, false) == Event::None);
    assert(open.progress() > 0);
    assert(open.update(true, 1930, false) == Event::Open);
    assert(open.progress() == 0);
    assert(open.update(true, 2500, true) == Event::None);
    assert(open.update(false, 2501, true) == Event::None);
    assert(open.update(false, 2531, true) == Event::None);

    Controller confirm;
    settle(confirm, true, 3000, true);
    assert(confirm.update(true, 3930, true) == Event::Confirm);
    assert(confirm.update(false, 3931, true) == Event::None);
    assert(confirm.update(false, 3961, true) == Event::None);

    Controller next;
    settle(next, true, 4000, true);
    assert(next.update(false, 4200, false) == Event::None);
    assert(next.update(false, 4230, false) == Event::Next);

    Controller wrap;
    assert(wrap.update(false, 0xfffffff0u, false) == Event::None);
    assert(wrap.update(true, 0xfffffff5u, false) == Event::BeginHold);
    assert(wrap.update(true, 0x00000013u, false) == Event::None);
    assert(wrap.holding());
    assert(wrap.update(true, 0x00000397u, false) == Event::Open);

    Controller boundary;
    settle(boundary, true, 0, false);
    assert(boundary.update(false, 925, false) == Event::None);
    assert(boundary.update(false, 930, false) == Event::None);
    assert(boundary.update(false, 955, false) == Event::Theme);
}
