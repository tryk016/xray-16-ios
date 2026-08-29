<div align="center">
  <img src="misc/media/OpenXRayCover.png" alt="OpenXRay" />
</div>

# OpenXRay iOS

This repository contains an iOS port of the OpenXRay engine for
**S.T.A.L.K.E.R.: Call of Pripyat 1.6.02**. It targets arm64 iPhones running
iOS 16.4 or newer and renders through OpenGL ES 3.0 on Apple's GL-on-Metal
implementation.

The port is playable as a development build, but it is not ready for general
distribution or the App Store. This branch is intentionally iOS-only; desktop
compatibility is not one of its release requirements.

## Current status

- The engine builds and runs the menu and 3D gameplay on a physical iPhone.
- The current device baseline renders at native 1864x860 without a final image
  upscale.
- The offline gate checks 279 shader stages and 137 linked shader pairs.
- Retail Call of Pripyat data is loaded from the app's Documents container.
- MFi and Bluetooth controllers are the primary gameplay input. Touch currently
  covers menu pointer movement and taps.
- Saves and QuickLoad pass the controlled physical Zaton scenario. Additional
  saves, interiors, level transitions and long sessions still need coverage.

The exact accepted state and its evidence are recorded in
[the iOS port specification](doc/iOS-Port.md).

## Requirements

- An Apple-silicon Mac
- Xcode 27 with the iOS command-line tools
- CMake
- An iPhone running iOS 16.4 or later for physical-device validation
- Local Apple Development signing for device installation
- A legal copy of Call of Pripyat 1.6.02
- Git submodules checked out

After cloning the repository:

```sh
git submodule update --init --recursive
```

## Build and test

Use the logged fast gate for normal local work:

```sh
./misc/ios/run_gate_logged.sh fast
```

It builds the FastDevice variant and runs the relevant host checks without
installing anything. After the final commit of an engine or shader change, run
the complete release gate before publishing:

```sh
./misc/ios/run_gate_logged.sh full
```

The full gate performs the complete retail, shader and link checks and creates
a stamped Release artifact. Signing can be checked without touching a phone:

```sh
./misc/ios/install_device.sh --preflight
```

To install a validated FastDevice build during local development:

```sh
./misc/ios/install_device.sh --fast --launch
```

Physical-device tools use a shared lease and refuse to run while another
project owns the phone. Simulator runs are useful for controlled data-flow and
UI evidence, but they do not prove iPhone rendering, memory use, thermals or
frame pacing.

## Retail data

This repository does not include or download Call of Pripyat archives, saves
or device containers.

You must own Call of Pripyat 1.6.02 and provide its data yourself. Keep all
retail assets and saves outside Git. The local import tools work only with a
backup that is already available to the developer; they are not a game-data
distribution mechanism.

OpenXRay is a fan project and is not affiliated with GSC Game World. Follow
GSC's [EULA](https://www.gsc-game.com/eula/) and
[Fan Content Creation Guidelines](https://www.gsc-game.com/guidelines/).

## Known limitations

- Wider device coverage is still needed for saves, interiors, portals and level
  transitions.
- Long-duration memory, thermal and frame-pacing targets have not been accepted.
- Lock/unlock and audio-interruption scenarios still need dedicated device
  validation.
- Virtual touch controls for gameplay are incomplete.
- Tester distribution and signed-update preservation are not complete.
- ANGLE and a native Metal renderer remain research options, not active
  migrations.

## Project documentation

- [Current iOS specification](doc/iOS-Port.md)
- [Active roadmap](doc/iOS-Port-Plan.md)
- [Operational handoff](doc/iOS-Port-Resume.md)
- [Deferred backlog](doc/iOS-Port-Backlog.md)
- [Development journal](doc/iOS-Port-Journal.md)
- [Contributor and agent workflow](AGENTS.md)

## License and credits

This port builds on the work of the
[OpenXRay project](https://github.com/OpenXRay/xray-16), its contributors and
the original X-Ray engine authors. Source code in this repository is available
under the [MIT License](License.txt); third-party components retain their own
licenses.

S.T.A.L.K.E.R. and Call of Pripyat, including all retail game data, remain the
property of their respective rights holders.
