# CLAUDE.md

Read `README.md` for hardware, protocol and wiring. This file covers how to operate it.

Handing this tool to another Claude session: the user runs `./relay.py --claude-prompt`
and pastes the output into that session (it points the session at this directory and
tells it to read this file and the README).

## What this is

`relay.py` drives an LCUS-1 USB relay (CH340, `/dev/relay0`) from CLI commands. On this
HIL rig the relay switches power to a Raspberry Pi DUT, but this tool never connects to
the DUT. It only drives the relay; shutdown and boot checks belong to the caller.

## First session on a new machine

1. Check the relay is visible: `lsusb | grep 1a86:7523` and `ls -l /dev/relay0`.
2. If `/dev/relay0` is missing: ask the user, then run `./relay.py setup` (needs sudo,
   so the user may need to run it themselves with `! ./relay.py setup`). It waits for
   the relay and says if it must be plugged in. No logout is needed afterwards. If
   `lsusb` shows the device but no tty appears, `brltty` is the usual cause (see README
   Troubleshooting).
3. If a DUT is (or will be) wired up, ask whether COM/NC polarity was confirmed with a
   multimeter. The README flags it as unverified.
4. Smoke test (safe only if the user confirms the DUT is shut down or disconnected):
   `./relay.py cycle --hold 1`, then ask the user if they heard two clicks. You cannot
   verify this yourself.

## Commands

- `./relay.py cycle [--hold N]` power-cycle the DUT (default 5 s cut, ends powered).
  Normal use.
- `./relay.py cut` / `restore` cut / restore the DUT's power (NC wiring in the README).
  Always follow `cut` with `restore`. `on` / `off` are aliases for `cut` / `restore`
  (raw coil state), so `on` CUTS power; prefer `cut` / `restore`.
- The relay boots de-energized, so after a replug or host reboot the DUT is powered.
- `./relay.py setup` one-time host setup. Needs sudo; ask the user before running it.

There is no `status`: the board gives no feedback, so never assume the relay's state
from earlier commands.

## Rules

- Prefer `cycle` over `cut`/`restore` so power is always restored. If you ever leave the
  relay `cut` (DUT unpowered), say so explicitly to the user.
- A power cut can corrupt the DUT's storage. If the user's task involves a DUT, check
  with them whether it should be shut down cleanly first.
- `cycle` does not wait for the DUT to boot. If asked to confirm it came back, use
  whatever access the user gives you, with a timeout. Do not retry in a loop that keeps
  cutting power.
- Ask the user before anything beyond `cycle`/`cut`/`restore` on the DUT: changing wiring
  assumptions, connecting any other load to the relay, or running `setup`.
- Do not edit the protocol bytes or baud rate without hardware to verify against.

## If something fails

Follow the Troubleshooting table in `README.md`. Common causes: `brltty` installed,
user not in `dialout`, relay unplugged. The script prints an actionable error.

## Development

Single file, standard library only. Keep it that way. Test with the relay attached and
nothing wired to it; the click is the only confirmation available.
