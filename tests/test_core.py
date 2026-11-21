import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from m3u_parser import parse_m3u
from ffmpeg_manager import FFmpegProcess, probe_url, redact_url


class M3UParserTests(unittest.TestCase):
    def test_commas_in_attributes_and_single_quotes(self):
        text = (
            '#EXTM3U\n'
            '#EXTINF:-1 group-title="News, World" tvg-id="n1",Channel, One\n'
            'http://example/1\n'
            "#EXTINF:-1 group-title='Sports',Two\n"
            'http://example/2\n'
        )
        fd, path = tempfile.mkstemp(suffix=".m3u")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            channels = parse_m3u(path)
            self.assertEqual(len(channels), 2)
            self.assertEqual(channels[0].name, "Channel, One")
            self.assertEqual(channels[0].group, "News, World")
            self.assertEqual(channels[1].name, "Two")
            self.assertEqual(channels[1].group, "Sports")
        finally:
            os.unlink(path)


class FFmpegHelpersTests(unittest.TestCase):
    def test_mpts_program_streams_are_global(self):
        proc = FFmpegProcess("test", ["a", "b"], "udp://239.0.0.1:1234")
        proc._stream_types = [{"video", "audio"}, {"video", "audio"}]
        cmd = proc.build_command()
        self.assertIn("title=Channel_1:st=0:st=1", cmd)
        self.assertIn("title=Channel_2:st=2:st=3", cmd)

    def test_redact_url(self):
        self.assertEqual(
            redact_url("http://user:secret@example.com/live"),
            "http://user:***@example.com/live",
        )

    def test_probe_nonzero_without_stderr_is_failure(self):
        result = SimpleNamespace(returncode=1, stdout="", stderr="")
        with patch("ffmpeg_manager.subprocess.run", return_value=result):
            ok, message = probe_url("http://example/x", "ffmpeg")
        self.assertFalse(ok)
        self.assertIn("Код 1", message)

    def test_probe_success(self):
        result = SimpleNamespace(returncode=0, stdout="", stderr="")
        with patch("ffmpeg_manager.subprocess.run", return_value=result):
            ok, message = probe_url("http://example/x", "ffmpeg")
        self.assertTrue(ok)
        self.assertEqual(message, "")

    def test_mpts_program_mapping_without_audio(self):
        proc = FFmpegProcess("test", ["a", "b"], "udp://239.0.0.1:1234")
        proc._stream_types = [{"video"}, {"video", "audio"}]
        cmd = proc.build_command()
        self.assertIn("title=Channel_1:st=0", cmd)
        self.assertIn("title=Channel_2:st=1:st=2", cmd)


if __name__ == "__main__":
    unittest.main()
