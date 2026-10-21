# lcus-relay-control

Command-line control for an **LCUS-1 style USB relay** (model seen: `LCUS_NY_X1_V1.1`,
CH340 USB-serial chip, USB ID `1a86:7523`). Single file, Python 3 standard library only.

Primary use here: power-cycling a Raspberry Pi device-under-test (DUT) from a central
PC in a hardware-in-the-loop (HIL) test platform. The script itself is generic: it
energizes and releases a relay coil, and what the contacts switch is up to the wiring.

## Quick start

To give an already-running Claude session the context for this tool, run
`./relay.py --claude-prompt` and paste its output into that session.

```
./relay.py setup        # one-time per host (asks for sudo); waits until the relay is found
./relay.py cycle        # energize coil for 5 s (DUT unpowered), then release
./relay.py cycle --hold 10
./relay.py cut          # cut DUT power (energize coil)
./relay.py restore      # restore DUT power (release coil)
# `on` / `off` are aliases for `cut` / `restore` (raw coil state)
```

Options: `--port` (default `$RELAY_PORT` or `/dev/relay0`), `--hold SECONDS` for
`cycle`, `--user NAME` for `setup`, `--claude-prompt` to print the text to paste into a
Claude session (exits `0`, needs no relay).

## How it works

- The relay enumerates as a CH340 serial port (`/dev/ttyUSB*`) at **9600 baud, 8N1**.
- Protocol is a 4-byte frame `A0 01 <state> <checksum>`:
  - cut / on (coil energized, COM-NC open, DUT unpowered): `A0 01 01 A2`
  - restore / off (coil released, COM-NC closed, DUT powered): `A0 01 00 A1`
- The board **sends no feedback**. The script cannot read relay state, so there is no
  `status` or `toggle`. `cut` means "the command was sent".
- `cut`, `restore` and `cycle` are serialized with a lock file (`/tmp/relay.lock`), so
  concurrent callers cannot interleave commands. The file is created by whichever user
  runs first, so run all callers as the same user. A different user gets
  `error: cannot open lock file ...`; run as the first user, or delete the file if no
  relay command is running.
- `cycle` sends `cut`, waits `--hold` seconds, then always sends `restore`, including if the
  wait is interrupted (e.g. Ctrl-C). If the first `cut` fails, nothing was changed.
- `setup` writes `/etc/udev/rules.d/99-relay.rules`, which gives the relay the stable
  name `/dev/relay0` with group `dialout`, and adds the user to `dialout`. It then waits
  up to 60 s for the relay port to appear (`--port` to wait on a different path) and
  confirms access. No replug is needed if the relay is already plugged in; if it is not,
  `setup` tells you to plug it in. If the relay's USB chip is present but no port
  appears, it points at `brltty`; it also warns, before waiting, if `brltty` is installed.
  `setup` fails with an error if the port still has not appeared after 60 s.
- Right after `setup`, your current terminal does not yet have the new `dialout` group.
  When opening the port is denied for that reason, `relay.py` re-runs itself under
  `sg dialout` (guarded by the `RELAY_SG` environment variable so it only happens once),
  so it works without logging out. Other programs (e.g. `screen`) need a fresh login.
- Exit codes: `0` success; `1` an error, with a message on stderr (relay not found, no
  permission, lock file owned by another user, setup failed or sudo step failed); `2`
  no action given (help is printed) or an invalid argument (e.g. negative `--hold`).
- `_install` is an internal action that `setup` runs under sudo to do the root-only
  work. Do not call it directly; it refuses to run as a non-root user.

## Wiring (HIL power-cycling)

Wire the DUT's **DC supply (5 V side)** through **COM and NC** (normally closed):

- `restore` (coil released, `off`) = DUT powered
- `cut` (coil energized, `on`) = DUT unpowered
- `cycle` = DUT power cut for `--hold` seconds, then restored (relay left off)

Consequence of NC: the relay boots de-energized, so after a host reboot, a replug or a
USB drop the DUT **stays powered**. The coil is energized only while power is being cut.
If `cycle` is killed uncleanly (e.g. SIGKILL) during the hold, the DUT stays unpowered
until `./relay.py restore` runs. The `on`/`off` aliases follow the coil, so `on` cuts
DUT power; prefer `cut`/`restore`.

Do not switch mains voltage unless the board is rated for it and wired safely. Check the
ratings printed on your board (typically 10 A at 250 VAC / 30 VDC for LCUS-1).

## Notes for callers (this tool does not talk to the DUT)

This repo only switches the relay. It never connects to the DUT, so anything about
shutting it down cleanly or waiting for it to boot is the caller's job:

- A hard power cut can corrupt a Pi's SD card. Shut the DUT down first when a clean
  state matters; hard cuts suit crash-recovery tests.
- Keep `--hold` at 3 s or more so capacitors drain and the DUT reboots cleanly.
- `cycle` returns as soon as power is restored. It does not wait for the DUT to boot,
  so poll the DUT yourself rather than relying on a fixed sleep.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `lsusb` shows `1a86:7523` but no `/dev/ttyUSB*` | `brltty` (Braille service) claims CH340 devices and disconnects them. `sudo apt remove brltty`, then replug. Kernel log shows `claimed by ch341 while 'brltty' sets config`. |
| `error: relay not found` | Relay unplugged, or `setup` not run on this machine. Plug it in, or run `./relay.py setup`. Fall back to `--port /dev/ttyUSB0`. |
| `error: no permission` | User not in `dialout`. Run `./relay.py setup`. If you already did and it still fails, log out and back in (or reboot). |
| Click but wrong behavior | Check NO/NC terminals with a multimeter in continuity mode. A click does not prove which contact closed. |
| No click, no error | Baud rate may differ (some variants use 115200), or the board uses a different frame format. Check the board's documentation. |

## Hardware reference

- Verified: command bytes and 9600 baud produce an audible click on `LCUS_NY_X1_V1.1`.
- Not verified: which contact actually closes for each command (the NC wiring above relies on this). The
  COM-NO/COM-NC labels come from a third-party script for this board family, not from a continuity
  test on this unit. Confirm with a multimeter before wiring a DUT. Also unverified:
  behavior under load, and multi-channel boards (this script sends channel 1 only, and
  other LCUS boards probably use a different channel byte).
- Relay boards with USB HID interfaces (dcttech, `16c0:05df`) are a different device
  class and are not supported here; use `usbrelay` for those.
