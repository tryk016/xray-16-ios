# Prior art: OpenGothic iOS controller (reuse for Phase 5)

The same developer wrote a from-scratch iOS controller + touch system for their
**OpenGothic** iOS build (local reference: `C:\opengothic ios\ios\CONTROLLER-TECHNICAL.md`
and `game/ui/`, `game/utils/` sources). It is a strong design to draw on for OpenXRay
Phase 5 (touch/controls). This file records what transfers and what does not, so the
investment isn't lost.

## The crux: SDL2 vs raw GameController.framework

- **OpenGothic (Tempest engine)** reads the pad **directly from `GameController.framework`**
  in Objective-C++ (`game/utils/gamepad.mm`), with **no SDL**.
- **OpenXRay** routes all input through **SDL2** (`src/xrEngine/xr_input.cpp`,
  `CInput::OnFrame`), which already has MFi/Bluetooth controller support (buttons, axes,
  triggers-as-axes, rumble, sensors) and internally wraps GameController.framework.

**Therefore: do NOT port the raw `gamepad.mm` backend** — it would create a second input
path alongside SDL and invite bugs. Feed OpenXRay's existing `SDL_GameController` path
instead. Keep `gamepad.mm` only as a reference if SDL2 turns out to miss an input on iOS.

## Reuse map

| OpenGothic component | Reuse | How for OpenXRay / STALKER |
|---|---|---|
| `gamepad.mm` raw GCExtendedGamepad backend | ❌ backend | superseded by SDL2; keep as edge-case reference |
| Consume model: newest analog snapshot + lossless digital-edge FIFO | 🟡 partial | SDL already gives ordered events + polled axis state; adopt the discipline |
| **Neutral-before-rearm** (input must return to neutral before re-arming) | ✅ rule | add to the touch/controller synthesizer; blocks menu→gameplay input leak |
| Context routing — exactly one active context | ✅ pattern | maps onto OpenXRay's `IInputReceiver` stack (`cbStack`) — top receiver owns input |
| **Virtual touch-pad overlay** (`game/ui/touchinput.cpp`) | ✅ highest value | STALKER has none; net-new. Layout differs (FPS vs 3rd-person) but the pattern/geometry port directly |
| `padsystemgesture` (tap vs hold reducer, constexpr tests) | ✅ | testable gesture-reducer pattern; port the concept |
| `[GAMEPAD]` config schema (deadZone, releaseZone, crossAxisGuard, triggerThreshold, lookSensitivity, invertY) | ✅ | map onto OpenXRay's existing `gamepad_*` cvars in `src/xrEngine/xr_ioc_cmd.cpp` |
| "Analog trigger vs configured threshold, not Apple `isPressed`" | ✅ rule | with SDL a trigger is an axis (0..32767); apply our own threshold — identical approach |
| "A hold never changes meaning mid-hold" + release-and-suppress on state change | ✅ rule | robustness discipline, port as-is |
| Quick-rings (`game/ui/quickring.cpp`) | 🟡 idea | Gothic item/spell-specific; the radial-select idea could be repurposed for STALKER weapon/med selection, not a copy |
| Combat semantics (`rebuildPadCombatAction`, melee L/R, block, bow) | ❌ | Gothic-specific; STALKER is firearms (attack=fire, aim=ADS) |

## Robustness rules worth adopting verbatim (engine-agnostic)

1. Inputs held across a UI transition / controller generation change / disconnect / app
   resume must **return to neutral before they can re-arm**.
2. A physical hold must **never change its action mid-hold**; if game state changes while a
   button is down, release the old action and suppress the new one until a real release.
3. Evaluate analog triggers against a **configured threshold**, not the platform's
   `isPressed`, so behavior matches the tuning value.
4. On `SDL_APP_WILLENTERBACKGROUND`, stop rendering before the app suspends (see Phase 3)
   — the same lifecycle discipline the OpenGothic build follows.

## STALKER-specific control scheme (to design in Phase 5)

STALKER CoP is a mouse-look FPS with a PDA/inventory. Unlike Gothic's melee rings, the
core needs: left-stick move, right-stick/right-drag look, fire (RT), aim/ADS (LT), reload,
use, crouch, jump, sprint, weapon 1–6 quick-select, PDA/inventory, and a soft keyboard for
console/text. Reuse OpenGothic's touch overlay + gesture reducer + config + robustness
rules; design STALKER's own button map and (optionally) a radial weapon/med selector.
