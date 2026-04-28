#!/usr/bin/env python3
"""Unit tests for the pure helpers in ssh_local_echo.

These don't touch the network or PTYs — just exercise the byte-stream
parsers (escape sequences, expected-echo stripping, BEL filtering).
Run with `python3 tests/test_units.py` from the project root.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ssh_local_echo import Wrapper, _consume_escape  # noqa: E402


class TestConsumeEscape(unittest.TestCase):
    def _check(self, data, expected_seq, expected_n):
        seq, n = _consume_escape(data, 0)
        self.assertEqual(seq, expected_seq)
        self.assertEqual(n, expected_n)

    def test_csi_arrow_up(self):
        self._check(b'\x1b[A', b'\x1b[A', 3)

    def test_csi_with_params(self):
        self._check(b'\x1b[1;5A', b'\x1b[1;5A', 6)

    def test_csi_delete(self):
        self._check(b'\x1b[3~', b'\x1b[3~', 4)

    def test_ss3_f1(self):
        self._check(b'\x1bOP', b'\x1bOP', 3)

    def test_extra_bytes_after_sequence(self):
        self._check(b'\x1b[A x', b'\x1b[A', 3)

    def test_lone_esc(self):
        self._check(b'\x1b', b'\x1b', 1)

    def test_two_byte_esc(self):
        self._check(b'\x1bX', b'\x1bX', 2)


class TestStripExpectedEcho(unittest.TestCase):
    def _check(self, expected, server, want_remaining, want_left):
        w = Wrapper([])
        w.expected_echo = bytearray(expected)
        out = w._strip_expected_echo(server)
        self.assertEqual(out, want_remaining)
        self.assertEqual(bytes(w.expected_echo), want_left)

    def test_exact_match(self):
        self._check(b'ls\r', b'ls\rhello\r\n', b'hello\r\n', b'')

    def test_cr_to_crlf(self):
        # Server's terminal driver appends LF after CR (ONLCR).
        self._check(b'ls\r', b'ls\r\nhello\r\n', b'hello\r\n', b'')

    def test_cr_to_lf_only(self):
        self._check(b'ls\r', b'ls\nhello\n', b'hello\n', b'')

    def test_partial(self):
        self._check(b'hello\r', b'hel', b'', b'lo\r')

    def test_partial_then_completes(self):
        w = Wrapper([])
        w.expected_echo = bytearray(b'hello\r')
        first = w._strip_expected_echo(b'hel')
        self.assertEqual(first, b'')
        self.assertEqual(bytes(w.expected_echo), b'lo\r')
        second = w._strip_expected_echo(b'lo\rworld')
        self.assertEqual(second, b'world')
        self.assertEqual(bytes(w.expected_echo), b'')

    def test_mismatch_clears_expectation(self):
        # Tab-completion injects bytes that don't match what we sent.
        self._check(b'cd Doc\t', b'cd Documents/', b'uments/', b'')

    def test_empty_expected(self):
        self._check(b'', b'output', b'output', b'')


class TestStripStandaloneBel(unittest.TestCase):
    def _run(self, chunks, start_in_osc=False):
        w = Wrapper([])
        w._in_osc = start_in_osc
        return b''.join(w._strip_standalone_bel(c) for c in chunks)

    def test_plain_bel_dropped(self):
        self.assertEqual(self._run([b'hello\x07world']), b'helloworld')

    def test_osc_bel_terminator_preserved(self):
        # ESC ] 0 ; title BEL ... — the BEL is the OSC string terminator.
        self.assertEqual(
            self._run([b'\x1b]0;title\x07rest']),
            b'\x1b]0;title\x07rest',
        )

    def test_osc_then_standalone_bel(self):
        self.assertEqual(
            self._run([b'\x1b]0;t\x07x\x07y']),
            b'\x1b]0;t\x07xy',
        )

    def test_osc_with_st_terminator(self):
        # ESC \ is the alternate ST terminator.
        self.assertEqual(
            self._run([b'\x1b]0;t\x1b\\rest']),
            b'\x1b]0;t\x1b\\rest',
        )

    def test_split_esc_then_open_bracket(self):
        # ESC at end of one chunk, ']' at start of next.
        self.assertEqual(
            self._run([b'\x1b', b']0;t\x07x']),
            b'\x1b]0;t\x07x',
        )

    def test_tab_response_bel_plus_completion(self):
        # readline rings the bell on tab and then sends the completion char.
        self.assertEqual(self._run([b'\x07d']), b'd')


if __name__ == '__main__':
    unittest.main(verbosity=2)
