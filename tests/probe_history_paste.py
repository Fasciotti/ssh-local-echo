#!/usr/bin/env python3
"""Verify history navigation, post-history edits, and bracketed paste.

Three scenarios:
1. Type a command, run it, press Up to recall, edit, run again.
2. Type prefix locally, then paste content (bracketed paste markers),
   then submit. Verifies pasted content appends to local prefix.
3. Press Ctrl-C in server-owned mode and verify state resets.
"""

import os
import pty
import select
import sys
import time

WRAPPER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       '..', 'ssh_local_echo.py')

UP = b'\x1b[A'
LEFT = b'\x1b[D'
END = b'\x1b[F'
PASTE_START = b'\x1b[200~'
PASTE_END = b'\x1b[201~'


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
        # Scenario 1: build history, then recall + edit
        (1.5, b'echo first-command\r', 'run "echo first-command"'),
        (1.5, b'echo second-command\r', 'run "echo second-command"'),
        (1.5, UP, 'Up arrow (recall "echo second-command")'),
        (1.0, END, 'End (cursor to end)'),
        (0.8, b' modified', 'append " modified"'),
        (0.8, b'\r', 'Enter (should run "echo second-command modified")'),
        # Scenario 2: paste with prefix
        (2.0, b'echo PASTED:', 'type "echo PASTED:"'),
        (0.8, PASTE_START, 'paste start'),
        (0.5, b'/etc/passwd content here', 'paste body'),
        (0.5, PASTE_END, 'paste end'),
        (1.0, b'\r', 'Enter (should run "echo PASTED:/etc/passwd content here")'),
        # Scenario 3: Ctrl-C resets server-owned state
        (1.5, UP, 'Up arrow (recall last)'),
        (1.0, b'\x03', 'Ctrl-C (cancel)'),
        (1.0, b'echo back-to-buffered\r', 'should be buffer-owned again'),
        (1.5, b'exit\r', 'exit'),
    ]

    next_step = 0
    last_action = time.time()
    deadline = time.time() + 80
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
