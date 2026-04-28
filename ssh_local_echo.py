#!/usr/bin/env python3
"""
SSH wrapper with local echo to mitigate latency on standard SSH servers.

Buffers keypresses locally and echoes them immediately to the terminal,
sending to the remote server only on Enter, Tab, Ctrl-{C,D,Z,L}, arrows,
and similar trigger keys. Toggle to passthrough mode with Ctrl-O for
full-screen applications (vim, less, top) or password prompts.

Designed to run on Linux / WSL (uses pty.fork + termios + select).
"""

import errno
import fcntl
import os
import pty
import select
import signal
import struct
import sys
import termios
import tty


CTRL_O = 0x0f
CR = 0x0d
LF = 0x0a
TAB = 0x09
BS = 0x08
DEL = 0x7f
ESC = 0x1b
ETX = 0x03
EOT = 0x04
SUB = 0x1a
FF = 0x0c
BEL = 0x07
OSC_OPEN = 0x5d  # ']'


class Wrapper:
    def __init__(self, ssh_args, start_passthrough=False, filter_bel=True):
        self.ssh_args = list(ssh_args)
        self.passthrough = start_passthrough
        self.filter_bel = filter_bel
        self.buffer = bytearray()
        self.expected_echo = bytearray()
        self.master_fd = -1
        self.child_pid = -1
        self.original_termios = None
        self.exit_status = 0
        # Output parser state — survives across read() chunks.
        self._in_osc = False         # inside ESC ] ... (BEL|ESC \) sequence
        self._pending_esc = False    # last byte of prior chunk was ESC

    # --- Lifecycle ------------------------------------------------------

    def run(self):
        try:
            cols, rows = os.get_terminal_size(0)
        except OSError:
            cols, rows = 80, 24

        pid, master_fd = pty.fork()
        if pid == 0:
            try:
                os.execvp('ssh', ['ssh'] + self.ssh_args)
            except OSError as exc:
                sys.stderr.write('exec ssh failed: %s\n' % exc)
                os._exit(127)
        self.child_pid = pid
        self.master_fd = master_fd
        self._set_remote_winsize(rows, cols)

        if sys.stdin.isatty():
            self.original_termios = termios.tcgetattr(sys.stdin)
            tty.setraw(sys.stdin.fileno())

        signal.signal(signal.SIGWINCH, self._on_resize)

        self._print_banner()

        try:
            self._loop()
        finally:
            self._cleanup()

        return self.exit_status

    def _print_banner(self):
        mode = b'passthrough' if self.passthrough else b'buffered'
        msg = (b'\r\n[ssh-local-echo] mode=' + mode +
               b'  toggle=Ctrl-O  (Ctrl-O switches buffered<->passthrough)\r\n')
        try:
            os.write(sys.stdout.fileno(), msg)
        except OSError:
            pass

    def _cleanup(self):
        if self.original_termios is not None:
            try:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN,
                                  self.original_termios)
            except (OSError, termios.error):
                pass
        if self.master_fd >= 0:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = -1
        if self.child_pid > 0:
            try:
                _, status = os.waitpid(self.child_pid, 0)
                if os.WIFEXITED(status):
                    self.exit_status = os.WEXITSTATUS(status)
                elif os.WIFSIGNALED(status):
                    self.exit_status = 128 + os.WTERMSIG(status)
            except (OSError, ChildProcessError):
                pass

    def _on_resize(self, _signum, _frame):
        try:
            cols, rows = os.get_terminal_size(0)
        except OSError:
            return
        self._set_remote_winsize(rows, cols)

    def _set_remote_winsize(self, rows, cols):
        if self.master_fd < 0:
            return
        try:
            winsize = struct.pack('HHHH', rows, cols, 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
        except OSError:
            pass

    # --- Main loop ------------------------------------------------------

    def _loop(self):
        stdin_fd = sys.stdin.fileno()
        stdout_fd = sys.stdout.fileno()
        while True:
            try:
                r, _, _ = select.select([stdin_fd, self.master_fd], [], [])
            except (OSError, select.error) as exc:
                if exc.args and exc.args[0] == errno.EINTR:
                    continue
                break

            if self.master_fd in r:
                try:
                    data = os.read(self.master_fd, 4096)
                except OSError:
                    data = b''
                if not data:
                    break
                self._handle_output(data, stdout_fd)

            if stdin_fd in r:
                try:
                    data = os.read(stdin_fd, 4096)
                except OSError:
                    data = b''
                if not data:
                    break
                self._handle_input(data, stdout_fd)

    # --- Input handling -------------------------------------------------

    def _handle_input(self, data, stdout_fd):
        i = 0
        n = len(data)
        while i < n:
            b = data[i]

            if b == CTRL_O:
                self._toggle_mode(stdout_fd)
                i += 1
                continue

            if self.passthrough:
                # Bulk-write up to the next CTRL_O or end of buffer.
                j = i
                while j < n and data[j] != CTRL_O:
                    j += 1
                os.write(self.master_fd, bytes(data[i:j]))
                i = j
                continue

            # --- buffered mode ---
            if b == ESC:
                seq, consumed = _consume_escape(data, i)
                # Arrows / function keys / etc: server-side handling.
                # Erase any locally-buffered prefix so display doesn't conflict
                # with whatever the server redraws (history navigation, etc.).
                self._send_immediate(seq, stdout_fd, erase_buffer=True)
                i += consumed
                continue

            if b in (CR, LF):
                self._send_with_buffer(b'\r', stdout_fd, local_echo=b'\r\n')
                i += 1
                continue

            if b == TAB:
                # Send buffer + tab, let server's completion output land
                # on screen unmodified. Local buffer cleared; server now owns
                # the line state until next Enter.
                self._send_with_buffer(b'\t', stdout_fd, local_echo=b'')
                i += 1
                continue

            if b in (BS, DEL):
                if self.buffer:
                    self.buffer.pop()
                    os.write(stdout_fd, b'\b \b')
                else:
                    # Local buffer empty: line state is owned by the server
                    # (e.g. after Tab completion or just-after-Enter). Forward
                    # the backspace so bash readline erases its own buffer.
                    os.write(self.master_fd, b'\x7f')
                i += 1
                continue

            if b == ETX:
                self._send_immediate(b'\x03', stdout_fd, erase_buffer=True)
                i += 1
                continue

            if b == SUB:
                self._send_immediate(b'\x1a', stdout_fd, erase_buffer=True)
                i += 1
                continue

            if b == EOT:
                # Send accumulated buffer + EOT so server sees the typed line.
                # Empty buffer → bare EOT (logout in bash).
                self._send_with_buffer(b'\x04', stdout_fd, local_echo=b'')
                i += 1
                continue

            if b == FF:
                # Clear-screen: forward without touching local buffer.
                # The visible buffer prefix may briefly be redrawn by the
                # server; if it isn't, the user can press Backspace through
                # their buffer or just press Enter.
                os.write(self.master_fd, b'\x0c')
                i += 1
                continue

            # Printable / data byte: append + local echo
            self.buffer.append(b)
            os.write(stdout_fd, bytes((b,)))
            i += 1

    def _send_with_buffer(self, suffix, stdout_fd, local_echo):
        send = bytes(self.buffer) + suffix
        self.expected_echo += send
        os.write(self.master_fd, send)
        self.buffer.clear()
        if local_echo:
            os.write(stdout_fd, local_echo)

    def _send_immediate(self, payload, stdout_fd, erase_buffer):
        if erase_buffer and self.buffer:
            os.write(stdout_fd, b'\b \b' * len(self.buffer))
            self.buffer.clear()
        os.write(self.master_fd, payload)

    # --- Output handling ------------------------------------------------

    def _handle_output(self, data, stdout_fd):
        if self.expected_echo and not self.passthrough:
            data = self._strip_expected_echo(data)
        if self.filter_bel and not self.passthrough:
            data = self._strip_standalone_bel(data)
        if data:
            os.write(stdout_fd, data)

    def _strip_standalone_bel(self, data):
        """Drop BEL (0x07) bytes that aren't terminating an OSC sequence.

        OSC strings (ESC ']' ... terminator) use either BEL or ESC '\' as
        terminator; we must keep those intact so window-title sequences and
        similar still work. Standalone BELs (typically from readline ringing
        on tab completion) are dropped to prevent the system bell from
        firing on every Tab press.

        State is kept on the instance so a sequence split across read()
        chunks is still parsed correctly.
        """
        out = bytearray()
        i = 0
        n = len(data)

        # Resolve any ESC carried over from the previous chunk.
        if self._pending_esc:
            self._pending_esc = False
            if n > 0 and data[0] == OSC_OPEN:
                out.append(ESC)
                out.append(data[0])
                self._in_osc = True
                i = 1
            else:
                out.append(ESC)

        while i < n:
            b = data[i]
            if self._in_osc:
                out.append(b)
                if b == BEL:
                    self._in_osc = False
                elif b == ESC and i + 1 < n and data[i + 1] == 0x5c:
                    out.append(data[i + 1])
                    self._in_osc = False
                    i += 2
                    continue
                i += 1
                continue
            if b == ESC:
                if i + 1 >= n:
                    # Defer decision to next chunk.
                    self._pending_esc = True
                    i += 1
                    continue
                if data[i + 1] == OSC_OPEN:
                    out.append(b)
                    out.append(data[i + 1])
                    self._in_osc = True
                    i += 2
                    continue
                out.append(b)
                i += 1
                continue
            if b == BEL:
                # Standalone BEL — drop.
                i += 1
                continue
            out.append(b)
            i += 1
        return bytes(out)

    def _strip_expected_echo(self, data):
        di = 0
        ei = 0
        e = self.expected_echo
        n = len(data)
        m = len(e)
        while di < n and ei < m:
            d = data[di]
            ec = e[ei]
            if d == ec:
                di += 1
                ei += 1
            elif ec == CR and d == LF:
                di += 1
                ei += 1
            elif d == LF and ei > 0 and e[ei - 1] == CR:
                # Server appended LF after the CR we already matched.
                di += 1
            else:
                # Echo diverged — drop our expectation rather than letting
                # stale state poison future output. Show the rest as-is.
                self.expected_echo.clear()
                return data[di:]
        if ei == m:
            # If our expected ended in CR, also swallow a trailing LF the
            # server's terminal driver may have appended (ONLCR).
            if m > 0 and e[m - 1] == CR and di < n and data[di] == LF:
                di += 1
            self.expected_echo.clear()
        else:
            self.expected_echo = e[ei:]
        return data[di:]

    # --- Mode toggle ----------------------------------------------------

    def _toggle_mode(self, stdout_fd):
        self.passthrough = not self.passthrough
        if self.passthrough:
            label = b'\r\n[ssh-local-echo: passthrough]\r\n'
            # Flush any locally-buffered chars to screen as a hint, but
            # don't send them — user toggled to raw mode mid-typing.
            if self.buffer:
                os.write(stdout_fd, b'\b \b' * len(self.buffer))
                self.buffer.clear()
        else:
            label = b'\r\n[ssh-local-echo: buffered]\r\n'
            self.expected_echo.clear()
        os.write(stdout_fd, label)


def _consume_escape(data, start):
    """Consume an ANSI escape sequence starting at data[start].

    Returns (bytes, consumed_count). Handles CSI (ESC[), SS3 (ESCO), and
    plain two-byte ESC sequences.
    """
    n = len(data)
    if start >= n or data[start] != ESC:
        return b'', 0
    i = start + 1
    if i >= n:
        return bytes(data[start:i]), i - start
    second = data[i]
    if second == 0x5b:  # '['
        i += 1
        while i < n and not (0x40 <= data[i] <= 0x7e):
            i += 1
        if i < n:
            i += 1
        return bytes(data[start:i]), i - start
    if second == 0x4f:  # 'O'
        i += 1
        if i < n:
            i += 1
        return bytes(data[start:i]), i - start
    return bytes(data[start:i + 1]), (i + 1) - start


USAGE = """\
usage: ssh-local-echo [--passthrough] [--keep-bell] [--help] [--] <ssh args...>

SSH wrapper with local echo to hide latency on standard SSH servers
(no server-side install needed).

Options (must come before ssh args):
  --passthrough   Start in passthrough mode (default: buffered)
  --keep-bell     Don't filter standalone BEL (0x07) in buffered mode.
                  By default, BELs outside OSC sequences are dropped to
                  silence readline tab-completion beeps.
  --help, -h      Show this help and exit
  --              End of wrapper options; everything after is ssh args

Toggle key during session: Ctrl-O (buffered <-> passthrough).

Example:
  ssh-local-echo -p 2220 bandit4@bandit.labs.overthewire.org
"""


def _split_args(argv):
    """Split argv into ((passthrough, filter_bel), ssh_args)."""
    passthrough = False
    filter_bel = True
    i = 1
    n = len(argv)
    while i < n:
        a = argv[i]
        if a == '--passthrough':
            passthrough = True
            i += 1
        elif a == '--keep-bell':
            filter_bel = False
            i += 1
        elif a in ('--help', '-h'):
            sys.stdout.write(USAGE)
            sys.exit(0)
        elif a == '--':
            i += 1
            break
        else:
            break
    return (passthrough, filter_bel), argv[i:]


def main():
    (passthrough, filter_bel), ssh_args = _split_args(sys.argv)
    if not ssh_args:
        sys.stderr.write(USAGE)
        sys.stderr.write('\nerror: missing ssh arguments '
                         '(e.g. -p 2220 user@host)\n')
        return 2

    wrapper = Wrapper(ssh_args, start_passthrough=passthrough,
                      filter_bel=filter_bel)
    return wrapper.run()


if __name__ == '__main__':
    sys.exit(main() or 0)
