"""Tests for upload_preset.py that need no MIDI hardware.

The transport is verified once on-device against a scratch slot. What is
testable here is slot parsing, the overwrite policy as written, the dry-run
path, and the no-TTY refusal in main().

Stdlib only: `python3 tests/test_upload_cli.py -v`
"""
import contextlib
import io
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent
                       / "skills" / "gp200-prst-export" / "scripts"))

import prst_upload  # noqa: E402
import upload_preset  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "web-export.prst"


class TestSlotParsing(unittest.TestCase):
    def test_labels_round_trip(self):
        for label, index in [("01-A", 0), ("01-D", 3), ("03-B", 9), ("32-D", 127)]:
            with self.subTest(label=label):
                self.assertEqual(upload_preset.parse_slot(label), index)

    def test_bad_labels_rejected(self):
        # "00-A" -> -4 and "65-A" -> 256 are the dangerous cases: both are
        # out of the device's 0-255 slot range but slot_label_to_index()
        # itself does not check that, so this has to be caught here.
        for bad in ["00-A", "65-A", "01-E", "1A", ""]:
            with self.subTest(label=bad):
                with self.assertRaises(ValueError):
                    upload_preset.parse_slot(bad)

    def test_high_bank_slots_rejected(self):
        """FIX 1 (CRITICAL): build_upload_chunks()'s chunk header only has a
        7-bit slot byte (`slot & 0x7F`), so any index above 127 -- bank 33-A
        and up -- silently aliases onto some other, in-range slot instead of
        the one the user confirmed. parse_slot() must reject these outright,
        even though slot_label_to_index() itself resolves them fine and
        01-64 is otherwise a "valid" device bank range."""
        for bad in ["33-A", "48-C", "64-D"]:
            with self.subTest(label=bad):
                with self.assertRaises(ValueError):
                    upload_preset.parse_slot(bad)

    def test_low_bank_slots_still_accepted(self):
        """The other half of FIX 1: 01-A through 32-D (index 0-127) must
        keep working normally -- the fix must not overcorrect into
        rejecting valid, addressable slots."""
        for label, index in [("01-A", 0), ("03-B", 9), ("32-D", 127)]:
            with self.subTest(label=label):
                self.assertEqual(upload_preset.parse_slot(label), index)


class TestOverwritePolicy(unittest.TestCase):
    """Policy as actually implemented: always prompt, except --force."""

    def test_force_true_never_prompts(self):
        for current_name in ("Song Intro", "", None):
            with self.subTest(current_name=current_name):
                self.assertIs(
                    upload_preset.should_prompt_for_overwrite(
                        current_name, force=True, interactive=True),
                    False)

    def test_force_false_always_prompts(self):
        for current_name in ("Song Intro", "", None):
            with self.subTest(current_name=current_name):
                self.assertIs(
                    upload_preset.should_prompt_for_overwrite(
                        current_name, force=False, interactive=True),
                    True)


class TestDryRun(unittest.TestCase):
    def test_reports_plan_without_midi(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = upload_preset.main([str(FIXTURE), "--slot", "03-B", "--dry-run"])
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("03-B", out)
        self.assertIn("7 chunks", out)
        self.assertIn("1184", out)
        self.assertIn("nothing sent", out)


class _NoTTYStdin:
    """Stands in for sys.stdin: answers isatty() honestly, blows up on
    read so the test fails loudly if main() ever tries to prompt anyway."""

    def isatty(self):
        return False

    def readline(self, *a, **k):
        raise AssertionError("main() must not read from stdin when it is not a tty")


class _TTYStdin:
    """Stands in for sys.stdin: reports a terminal is attached. input() is
    separately patched to supply the answer, so this only needs isatty()."""

    def isatty(self):
        return True


class TestNoTTYRefusal(unittest.TestCase):
    """FIX 2: should_prompt_for_overwrite() said yes, but there is no
    terminal to ask on. main() must refuse rather than call input(), and
    must not touch the device.

    Opening a real MIDI port and reading a slot name needs a GP-200 on
    USB, which this test environment does not have. Those two calls
    (open_device, read_slot_name) are mocked at the module boundary so
    the test exercises main()'s own no-TTY branch, the part that can be
    tested here; the transport underneath them is covered separately, by
    parity tests and by hand against real hardware, not faked here.
    """

    def test_refuses_without_calling_input(self):
        mock_inp, mock_out = mock.Mock(), mock.Mock()
        with mock.patch.object(upload_preset, "open_device",
                                return_value=(mock_inp, mock_out)) as m_open, \
             mock.patch.object(upload_preset, "handshake", return_value=None), \
             mock.patch.object(upload_preset, "read_slot_name",
                                return_value="Song Intro") as m_read, \
             mock.patch.object(upload_preset, "upload") as m_upload, \
             mock.patch("builtins.input",
                        side_effect=AssertionError("must not prompt with no tty")), \
             mock.patch.object(sys, "stdin", _NoTTYStdin()):
            out_buf, err_buf = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out_buf), \
                 contextlib.redirect_stderr(err_buf):
                rc = upload_preset.main([str(FIXTURE), "--slot", "03-B"])

        self.assertEqual(rc, 2)
        self.assertIn("--force", err_buf.getvalue())
        m_open.assert_called_once()
        m_read.assert_called_once()
        m_upload.assert_not_called()
        mock_inp.close.assert_called_once()
        mock_out.close.assert_called_once()


class TestInteractiveConfirm(unittest.TestCase):
    """The confirm/decline path through main() with a real (tty) prompt.

    This is the single most important behaviour in the file: the confirm
    gate is the only thing standing between a build and a wiped slot. As
    in TestNoTTYRefusal, open_device and read_slot_name are mocked at the
    module boundary (they need a real GP-200 on USB); upload() is mocked
    as a tripwire so each branch can be proven to reach it, or not, by
    itself rather than by inference from print output.
    """

    def _run(self, answer):
        mock_inp, mock_out = mock.Mock(), mock.Mock()
        out_buf = io.StringIO()
        with mock.patch.object(upload_preset, "open_device",
                                return_value=(mock_inp, mock_out)), \
             mock.patch.object(upload_preset, "handshake", return_value=None), \
             mock.patch.object(upload_preset, "read_slot_name",
                                return_value="Song Intro") as m_read, \
             mock.patch.object(upload_preset, "upload") as m_upload, \
             mock.patch("builtins.input", return_value=answer), \
             mock.patch.object(sys, "stdin", _TTYStdin()):
            with contextlib.redirect_stdout(out_buf):
                rc = upload_preset.main([str(FIXTURE), "--slot", "03-B"])
        return rc, m_upload, m_read, mock_inp, mock_out, out_buf.getvalue()

    def test_yes_confirms_and_uploads(self):
        """FIX 2: main() must flash the REQUESTED slot, not just call
        upload() at all. Mutating `upload(args.file, slot, ...)` to
        `upload(args.file, 0, ...)` left every prior version of this test
        green -- assert_called_once() alone never inspected the arguments,
        so a wrong-slot write would have shipped silently. Pin both the
        slot passed to upload() and the slot read_slot_name() was asked
        about, so they can't drift apart from the parsed --slot."""
        rc, m_upload, m_read, mock_inp, mock_out, out = self._run("y")
        self.assertEqual(rc, 0)
        expected_slot = upload_preset.parse_slot("03-B")
        m_read.assert_called_once_with(mock_inp, mock_out, expected_slot)
        m_upload.assert_called_once_with(FIXTURE, expected_slot, mock_inp, mock_out)
        mock_inp.close.assert_called_once()
        mock_out.close.assert_called_once()

    def test_no_declines_and_does_not_upload(self):
        rc, m_upload, m_read, mock_inp, mock_out, out = self._run("n")
        self.assertEqual(rc, 1)
        m_upload.assert_not_called()
        self.assertIn("aborted", out)
        mock_inp.close.assert_called_once()
        mock_out.close.assert_called_once()


class TestHandshakeIsMandatory(unittest.TestCase):
    """main() must complete the handshake before it reads or writes anything.

    This is the bug the first real hardware run hit. upload_preset.py opened
    the port, went straight to read_slot_name, got silence, printed
    "currently: (unknown)", and then flashed 7 chunks into a device that had
    never acknowledged anything. The preset never landed.

    The device ignores slot reads until the handshake's state dump has been
    requested, so ordering here is a correctness requirement, not tidiness.
    """

    def _run(self, answer="y"):
        """Drive main() with every hardware call mocked, recording order."""
        calls = []
        mock_inp, mock_out = mock.Mock(), mock.Mock()

        def rec(name, ret=None):
            def f(*a, **k):
                calls.append(name)
                return ret
            return f

        with mock.patch.object(upload_preset, "open_device",
                               side_effect=rec("open_device", (mock_inp, mock_out))), \
             mock.patch.object(upload_preset, "handshake",
                               side_effect=rec("handshake", 5)), \
             mock.patch.object(upload_preset, "read_slot_name",
                               side_effect=rec("read_slot_name", "Old Patch")), \
             mock.patch.object(upload_preset, "upload", side_effect=rec("upload")), \
             mock.patch("builtins.input", return_value=answer), \
             mock.patch.object(upload_preset.sys, "stdin",
                               mock.Mock(isatty=lambda: True)), \
             contextlib.redirect_stdout(io.StringIO()):
            rc = upload_preset.main([str(FIXTURE), "--slot", "03-B"])
        return rc, calls

    def test_handshake_runs_before_any_read(self):
        _, calls = self._run()
        self.assertIn("handshake", calls, "main() never handshook")
        self.assertIn("read_slot_name", calls)
        self.assertLess(calls.index("handshake"), calls.index("read_slot_name"),
                        f"handshake must precede the slot read; got {calls}")

    def test_handshake_runs_before_any_write(self):
        _, calls = self._run(answer="y")
        self.assertIn("upload", calls)
        self.assertLess(calls.index("handshake"), calls.index("upload"),
                        f"handshake must precede the flash write; got {calls}")

    def test_handshake_runs_after_the_port_is_open(self):
        _, calls = self._run()
        self.assertLess(calls.index("open_device"), calls.index("handshake"))

    def test_declining_still_handshakes_but_never_writes(self):
        rc, calls = self._run(answer="n")
        self.assertEqual(rc, 1)
        self.assertIn("handshake", calls)
        self.assertNotIn("upload", calls)

class TestReadSlotNameNeverSendsNameOnlyRequest(unittest.TestCase):
    """read_slot_name must send sub=0x10 and nothing else.

    On hardware (2026-09-18) a single sub=0x20 name-only request wedges the
    GP-200: it stops answering everything afterwards, including the identity
    query, and a fresh handshake does not recover it. read_slot_name used to
    try sub=0x20 first and fall back to sub=0x10, which is why the overwrite
    prompt read "(unknown)" and the first real upload was sent into a device
    that had stopped listening.

    This test fails if anyone reintroduces the fast path.
    """

    def _capture_sent(self):
        sent = []
        mock_inp, mock_out = mock.Mock(), mock.Mock()
        mock_inp.iter_pending.return_value = []
        with mock.patch.object(upload_preset, "_send",
                               side_effect=lambda out, raw: sent.append(bytes(raw))):
            upload_preset.read_slot_name(mock_inp, mock_out, 127, timeout=0.05)
        return sent

    def test_sends_exactly_one_request(self):
        self.assertEqual(len(self._capture_sent()), 1,
                         "read_slot_name must issue a single request")

    def test_that_request_is_sub_0x10(self):
        sent = self._capture_sent()
        self.assertEqual(sent[0][8], 0x11)
        self.assertEqual(sent[0][9], 0x10, "sub must be 0x10, never 0x20")

    def test_never_sends_the_wedging_name_only_request(self):
        wedger = prst_upload.build_name_read_request(127)
        for raw in self._capture_sent():
            self.assertNotEqual(raw, wedger,
                                "sub=0x20 wedges the device; never send it")
            self.assertNotEqual(raw[9], 0x20)

class TestUploadWritesByLiveEditing(unittest.TestCase):
    """upload() writes via live editing, not the flash-write chunk burst.

    Measured on hardware 2026-09-18: the 0x12/0x20 chunk burst persists
    nothing. The slot keeps its previous effect payload and only the
    save-commit's name lands, which is why a name-only read-back reported
    success. The device does implement live editing, so upload() sets each
    block's effect type, parameters and bypass state, then save-commits.

    A save-commit and a parameter change are both 62-byte 0x12/0x18 messages;
    they are told apart by the decoded payload (commit starts 03 20 14).
    """

    def _sent_messages(self, tmpfile):
        sent = []
        mock_inp, mock_out = mock.Mock(), mock.Mock()
        mock_inp.iter_pending.return_value = []
        with mock.patch.object(upload_preset, "_send",
                               side_effect=lambda out, raw: sent.append(bytes(raw))), \
             mock.patch.object(upload_preset, "read_slot_image",
                               return_value=bytearray(upload_preset.READ_IMAGE_LEN)), \
             mock.patch.object(upload_preset, "diff_against_device",
                               return_value=[]), \
             mock.patch.object(upload_preset.time, "sleep"), \
             contextlib.redirect_stdout(io.StringIO()):
            upload_preset.upload(tmpfile, 127, mock_inp, mock_out)
        return sent

    @staticmethod
    def _commits(sent):
        out = []
        for m in sent:
            if len(m) == 62 and m[8] == 0x12 and m[9] == 0x18:
                decoded = prst_upload.nibble_decode(m[13:61])
                if decoded[0] == 0x03 and decoded[1] == 0x20:
                    out.append(decoded)
        return out

    def test_sends_no_flash_write_chunks(self):
        sent = self._sent_messages(FIXTURE)
        chunks = [m for m in sent
                  if m[8] == 0x12 and m[9] == 0x20 and len(m) > 100]
        self.assertEqual(chunks, [],
                         "the chunk burst persists nothing on this firmware")

    def test_a_save_commit_is_sent(self):
        self.assertEqual(len(self._commits(self._sent_messages(FIXTURE))), 1)

    def test_commit_comes_last(self):
        sent = self._sent_messages(FIXTURE)
        commit_positions = [
            i for i, m in enumerate(sent)
            if len(m) == 62 and m[8] == 0x12 and m[9] == 0x18
            and prst_upload.nibble_decode(m[13:61])[0] == 0x03
        ]
        self.assertEqual(commit_positions, [len(sent) - 1])

    def test_commit_carries_the_files_own_preset_name(self):
        decoded = self._commits(self._sent_messages(FIXTURE))[0]
        name = bytes(decoded[8:24]).split(b"\x00")[0].decode()
        self.assertEqual(name,
                         prst_upload.preset_name_from_file(FIXTURE.read_bytes()))

    def test_commit_targets_the_requested_slot(self):
        self.assertEqual(self._commits(self._sent_messages(FIXTURE))[0][4], 127)

    def test_sets_every_block_type_and_bypass_state(self):
        sent = self._sent_messages(FIXTURE)
        type_blocks = {m[38] for m in sent
                       if len(m) == 54 and m[8] == 0x12 and m[9] == 0x14}
        toggle_blocks = {m[38] for m in sent
                         if len(m) == 46 and m[8] == 0x12 and m[9] == 0x10
                         and m[29] == 0x01}
        self.assertEqual(type_blocks, set(range(11)))
        self.assertEqual(toggle_blocks, set(range(11)))

    def test_selects_the_target_slot_before_editing(self):
        sent = self._sent_messages(FIXTURE)
        self.assertEqual(sent[0], prst_upload.build_preset_change(127))


if __name__ == "__main__":
    unittest.main()
