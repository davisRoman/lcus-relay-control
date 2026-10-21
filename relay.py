#!/usr/bin/env python3
"""Control an LCUS-1 style (CH340) USB relay, e.g. to power-cycle a DUT.

Commands:
  setup     one-time host setup; installs the udev rule and dialout access,
            then waits until the relay is detected (asks for sudo)
  cut       cut DUT power (energize the coil; NC wiring)
  restore   restore DUT power (de-energize the coil)
  cycle     cut power for --hold seconds (default 5), then restore it
  on, off   aliases for cut, restore (raw coil state: on = energized)

Examples:
  ./relay.py --claude-prompt    # print a message to paste into a Claude session
  ./relay.py setup              # first time on a new machine
  ./relay.py cycle              # power-cycle the DUT (5 s unpowered, then powered)
  ./relay.py cycle --hold 10
  ./relay.py cut                # leaves the DUT unpowered until `restore`

Wire the DUT supply through COM/NC: "restore" (coil off) = DUT powered, "cut"
(coil on) = DUT unpowered. The relay boots de-energized, so the DUT stays powered after a host
reboot or replug. "cycle" cuts power for --hold seconds and restores it.
Concurrent invocations are serialized with a lock.

See README.md for wiring, protocol and troubleshooting.
"""
import argparse
import fcntl
import glob
import grp
import os
import pwd
import shlex
import shutil
import subprocess
import sys
import termios
import time

COIL_ON = bytes([0xA0, 0x01, 0x01, 0xA2])
COIL_OFF = bytes([0xA0, 0x01, 0x00, 0xA1])
# NC wiring: energizing the coil cuts DUT power. on/off are raw-coil aliases.
CMDS = {"cut": COIL_ON, "restore": COIL_OFF, "on": COIL_ON, "off": COIL_OFF}
DEFAULT_PORT = os.environ.get("RELAY_PORT", "/dev/relay0")
LOCK_FILE = "/tmp/relay.lock"
REPO_URL = "https://github.com/davisRoman/lcus-relay-control"
USB_ID = ("1a86", "7523")  # CH340 USB-serial chip on the LCUS-1 board
UDEV_RULE_PATH = "/etc/udev/rules.d/99-relay.rules"
UDEV_RULE = (
    "# LCUS-1 (CH340) USB relay -> stable name, accessible to dialout group\n"
    'SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="7523", '
    'SYMLINK+="relay0", GROUP="dialout", MODE="0660"\n'
)


def claude_prompt():
    """Message to paste into an already-running Claude session so it can use this tool."""
    here = os.path.dirname(os.path.abspath(__file__))
    return f"""There is a USB relay attached to this machine that I use to power-cycle a
Raspberry Pi. The tooling is in {here}
(source: {REPO_URL}).
Before touching the relay, read CLAUDE.md and README.md in that directory in full and
follow them whenever you operate it. Run the "First session on a new machine" steps;
ask me before running setup, since it needs sudo.
If that directory does not exist, clone {REPO_URL} first."""


def usb_relay_present():
    """True if the relay's USB chip is attached, even if no serial port exists."""
    for dev in glob.glob("/sys/bus/usb/devices/*"):
        try:
            ids = (open(f"{dev}/idVendor").read().strip(),
                   open(f"{dev}/idProduct").read().strip())
        except OSError:
            continue
        if ids == USB_ID:
            return True
    return False


def wait_for_port(port, timeout=60):
    """Wait for the relay's serial port to appear, guiding the user if it doesn't."""
    deadline = time.time() + timeout
    start = time.time()
    told_plug = told_brltty = False
    while time.time() < deadline:
        if os.path.exists(port):
            return True
        waited = time.time() - start
        if waited > 3:  # give udev a moment before bothering the user
            if usb_relay_present():
                if not told_brltty:
                    told_brltty = True
                    print("The relay is plugged in but no serial port appeared.\n"
                          "The usual cause is the 'brltty' package, which grabs CH340 "
                          "devices.\n  Fix: sudo apt remove brltty   (then unplug and "
                          "replug the relay)\nWaiting...")
            elif not told_plug:
                told_plug = True
                print(f"Relay not detected. Plug in the USB relay now "
                      f"(waiting up to {timeout} s)...")
        time.sleep(0.5)
    return False


def in_dialout_db_but_not_session():
    """True if the user was added to dialout but this shell predates that."""
    if os.environ.get("RELAY_SG"):  # already re-executed once; don't loop
        return False
    try:
        g = grp.getgrnam("dialout")
        user = pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return False
    return user in g.gr_mem and g.gr_gid not in os.getgroups()


def reexec_with_dialout():
    """Re-run this command with the dialout group active (no logout needed)."""
    cmd = shlex.join([sys.executable, os.path.abspath(__file__)] + sys.argv[1:])
    os.environ["RELAY_SG"] = "1"
    os.execvp("sg", ["sg", "dialout", "-c", cmd])


def install(user):
    """Root part of setup: write the udev rule, reload udev, add user to dialout."""
    try:
        with open(UDEV_RULE_PATH, "w") as f:
            f.write(UDEV_RULE)
        print(f"[1/3] wrote {UDEV_RULE_PATH}")
        subprocess.run(["udevadm", "control", "--reload"], check=True)
        subprocess.run(["udevadm", "trigger"], check=True)
        print("[2/3] reloaded udev rules")
        subprocess.run(["usermod", "-aG", "dialout", user], check=True)
        print(f"[3/3] added {user} to the dialout group")
    except (OSError, subprocess.CalledProcessError) as e:
        sys.exit(f"error: setup step failed: {e}\n  Fix the cause above, then re-run: ./relay.py setup")


def setup(user, port):
    """One-time host setup, then confirm the relay is detected and usable."""
    print("Setting up USB relay access (needs sudo)...")
    try:
        subprocess.run(["sudo", sys.executable, os.path.abspath(__file__),
                        "_install", "--user", user], check=True)
    except subprocess.CalledProcessError:
        sys.exit("error: the sudo install step failed (wrong password or cancelled?).\n"
                 "Re-run: ./relay.py setup")
    if shutil.which("brltty") or os.path.exists("/usr/lib/udev/rules.d/85-brltty.rules"):
        print("WARNING: 'brltty' is installed and grabs CH340 devices. If the relay "
              "is not\ndetected below, run: sudo apt remove brltty")
    if not wait_for_port(port):
        sys.exit(f"error: relay still not detected at {port} after 60 s.\n"
                 "See README.md > Troubleshooting, then re-run: ./relay.py setup")
    print(f"Found relay at {port} -> {os.path.realpath(port)}")
    if os.access(port, os.R_OK | os.W_OK):
        print("Access OK.")
    elif in_dialout_db_but_not_session():
        print("Access OK. (This terminal started before you joined 'dialout', so "
              "relay.py\nwill apply the group automatically. New logins get it natively.)")
    else:
        sys.exit(f"error: found {port} but cannot access it. Check that "
                 f"'{user}' is in the 'dialout' group: id {user}")
    print("\nSetup complete. Try it (listen for two clicks):  ./relay.py cycle --hold 1")


def send(port, data):
    try:
        fd = os.open(port, os.O_RDWR | os.O_NOCTTY)
    except FileNotFoundError:
        sys.exit(f"error: relay not found at {port}.\n"
                 "  - Not plugged in? Plug in the USB relay and retry.\n"
                 "  - First time on this machine? Run: ./relay.py setup")
    except PermissionError:
        if in_dialout_db_but_not_session():
            reexec_with_dialout()
        sys.exit(f"error: no permission to open {port}.\n"
                 "  Your user needs the 'dialout' group. Run: ./relay.py setup\n"
                 "  (if you already did, log out and back in, or reboot)")
    try:
        attrs = termios.tcgetattr(fd)
        attrs[0] = 0  # iflag
        attrs[1] = 0  # oflag
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL  # cflag
        attrs[3] = 0  # lflag (raw)
        attrs[4] = attrs[5] = termios.B9600
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        os.write(fd, data)
        termios.tcdrain(fd)
    finally:
        os.close(fd)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("action", nargs="?", metavar="{setup,cut,restore,cycle,on,off}",
                   choices=["setup", "cut", "restore", "cycle", "on", "off", "_install"])
    p.add_argument("--port", default=DEFAULT_PORT,
                   help="serial device (default: $RELAY_PORT or /dev/relay0)")
    p.add_argument("--hold", type=float, default=5.0,
                   help="seconds to hold power off during cycle (default 5)")
    p.add_argument("--user", default=os.environ.get("SUDO_USER") or os.environ.get("USER"),
                   help="user to give relay access during setup (default: you)")
    p.add_argument("--claude-prompt", action="store_true",
                   help="print a message to paste into a running Claude session so it "
                        "knows how to use this tool, then exit")
    args = p.parse_args()

    if args.hold < 0:
        p.error("--hold must be 0 or greater")
    if args.claude_prompt:
        print(claude_prompt())
        return
    if args.action is None:
        p.print_help()
        sys.exit(2)
    if args.action == "setup":
        setup(args.user, args.port)
        return
    if args.action == "_install":  # internal: run by `setup` under sudo
        if os.geteuid() != 0:
            sys.exit("error: '_install' is internal; run './relay.py setup' instead.")
        install(args.user)
        return

    try:
        lock = open(LOCK_FILE, "w")
    except PermissionError:
        sys.exit(f"error: cannot open lock file {LOCK_FILE}; it was created by another "
                 "user.\n  Run relay.py as that user, or delete the file if no relay "
                 "command is running.")
    with lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.action == "cycle":
            send(args.port, CMDS["cut"])
            try:
                time.sleep(args.hold)
            finally:
                send(args.port, CMDS["restore"])  # always restore power
        else:
            send(args.port, CMDS[args.action])


if __name__ == "__main__":
    main()
