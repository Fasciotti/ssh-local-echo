#!/usr/bin/env python3
"""Probe what bytes the server sends in response to Tab in various contexts.

Connects, logs in, then sends a series of Tab probes and dumps raw bytes
(escaped) so we can see exactly what the server emits — particularly any
BEL (0x07) characters.
"""

import os
import pty
import select
import sys
import time

WRAPPER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       '..', 'ssh_local_echo.py')


def escape(b):
    out = []
    for c in b:
        if c == 0x07:
            out.append('\\a[BEL]')
        elif c == 0x1b:
            out.append('\\e')
        elif c == 0x0d:
            out.append('\\r')
        elif c == 0x0a:
            out.append('\\n')
        elif c == 0x08:
            out.append('\\b')
        elif c == 0x09:
            out.append('\\t')
        elif 32 <= c < 127:
            out.append(chr(c))
        else:
            out.append('\\x%02x' % c)
    return ''.join(out)


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

    steps = [
        (1.5, b'\x0f', 'toggle->passthrough'),
        (1.0, b'bandit0\r', 'password'),
        (3.5, b'\x0f', 'toggle->buffered'),
        # Probe 1: tab on a unique-completing string (single match).
        (1.5, b'ls /etc/host', 'type "ls /etc/host"'),
        (1.0, b'\t', 'TAB (expect: completes to hosts/hostname/hostname.* — multi)'),
        (2.0, b'\x03', 'Ctrl-C to discard line'),
        # Probe 2: tab on something with a unique completion.
        (1.5, b'ls /etc/passw', 'type "ls /etc/passw"'),
        (1.0, b'\t', 'TAB (expect: completes to passwd uniquely)'),
        (2.0, b'\x03', 'Ctrl-C to discard line'),
        # Probe 3: tab on garbage with no completion.
        (1.5, b'ls /xyzzz', 'type "ls /xyzzz"'),
        (1.0, b'\t', 'TAB (expect: BEL — no match)'),
        (2.0, b'\x03', 'Ctrl-C to discard line'),
        (1.0, b'exit\r', 'exit'),
    ]

    next_step = 0
    last_action = time.time()
    deadline = time.time() + 60
    quiet_since = None

    print('--- raw bytes from wrapper stdout (escaped) ---')
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
            sys.stdout.write(escape(data))
            sys.stdout.flush()
            quiet_since = None

        if next_step < len(steps):
            delay, payload, label = steps[next_step]
            if time.time() - last_action >= delay:
                sys.stdout.write('\n--[' + label + ']--\n')
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
