#!/usr/bin/env python3
"""Drive ssh_local_echo.py through a PTY for end-to-end testing.

Sends a scripted sequence of keystrokes (with delays) and prints everything
the wrapper emits, so we can verify auth, prompt rendering, command output,
and echo suppression against a real server.
"""

import os
import pty
import select
import sys
import time

WRAPPER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       '..', 'ssh_local_echo.py')

STEPS = [
    # (delay_after_last_action_seconds, bytes_to_send, label_for_log)
    (1.5, b'\x0f', b'Ctrl-O -> passthrough (for password prompt)'),
    (1.0, b'bandit0\r', b'send password'),
    (3.5, b'\x0f', b'Ctrl-O -> buffered'),
    (0.8, b'whoami\r', b'whoami'),
    (1.5, b'echo hello-from-wrapper\r', b'echo'),
    (1.5, b'pwd\r', b'pwd'),
    (1.5, b'ls\r', b'ls'),
    (1.5, b'exit\r', b'exit shell'),
]


def main():
    pid, master = pty.fork()
    if pid == 0:
        os.execvp('python3', [
            'python3', WRAPPER,
            '-p', '2220',
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'PubkeyAuthentication=no',
            '-o', 'PreferredAuthentications=password',
            'bandit0@bandit.labs.overthewire.org',
        ])

    next_step = 0
    last_action = time.time()
    deadline = time.time() + 60
    quiet_since = None

    while time.time() < deadline:
        try:
            r, _, _ = select.select([master], [], [], 0.2)
        except OSError:
            break
        if master in r:
            try:
                data = os.read(master, 4096)
            except OSError:
                data = b''
            if not data:
                break
            sys.stdout.buffer.write(data)
            sys.stdout.flush()
            quiet_since = None

        if next_step < len(STEPS):
            delay, payload, label = STEPS[next_step]
            if time.time() - last_action >= delay:
                sys.stdout.buffer.write(b'\n--[driver: ' + label + b']--\n')
                sys.stdout.flush()
                try:
                    os.write(master, payload)
                except OSError:
                    break
                last_action = time.time()
                next_step += 1
        else:
            if quiet_since is None:
                quiet_since = time.time()
            elif time.time() - quiet_since > 4:
                break

    try:
        os.close(master)
    except OSError:
        pass
    try:
        os.waitpid(pid, os.WNOHANG)
    except OSError:
        pass


if __name__ == '__main__':
    main()
