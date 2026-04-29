#!/usr/bin/env python3
"""Verify local cursor movement (Left/Right/Home/End) and mid-line edits.

Logs into bandit0, types a command, navigates within it with arrows,
inserts and deletes mid-line, then sends Enter and verifies the server
ran what we expected.
"""

import os
import pty
import select
import sys
import time

WRAPPER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       '..', 'ssh_local_echo.py')

LEFT = b'\x1b[D'
RIGHT = b'\x1b[C'
HOME = b'\x1b[H'
END = b'\x1b[F'


def escape(b):
    out = []
    for c in b:
        if c == 0x07:
            out.append('\\a')
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
        elif c == 0x7f:
            out.append('\\x7f')
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
        # Type "echo HELLO" then arrow back, edit mid-line.
        (1.5, b'echo HELLO', 'type "echo HELLO"'),
        (0.8, LEFT * 5, 'Left x5 (cursor goes between echo and HELLO)'),
        (0.8, b'BIG-', 'insert "BIG-" mid-line (-> "echo BIG-HELLO")'),
        (0.8, END, 'End (cursor to end)'),
        (0.8, b'!', 'append "!" (-> "echo BIG-HELLO!")'),
        (0.8, HOME, 'Home (cursor to start)'),
        (0.8, RIGHT * 5, 'Right x5 (cursor between echo and BIG)'),
        (0.8, b'\x7f', 'Backspace (deletes the space -> "echoBIG-HELLO!")'),
        (0.8, b' ', 'insert space back -> "echo BIG-HELLO!"'),
        (0.8, b'\r', 'Enter (server should echo "BIG-HELLO!")'),
        (2.0, b'exit\r', 'exit'),
    ]

    next_step = 0
    last_action = time.time()
    deadline = time.time() + 60
    quiet_since = None

    print('--- raw bytes (escaped) ---')
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
