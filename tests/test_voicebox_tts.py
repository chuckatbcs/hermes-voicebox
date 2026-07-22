import sys
import os
import tempfile
import struct
import json
import threading
import unittest
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# Add scripts directory to sys.path
SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import voicebox_tts


class TestVoiceboxTTS(unittest.TestCase):

    def test_get_base_url_precedence(self):
        # 1. Default fallback
        orig_env = os.environ.pop("VOICEBOX_PORT", None)
        try:
            self.assertEqual(voicebox_tts.get_base_url(), "http://127.0.0.1:17493")

            # 2. Environment variable
            os.environ["VOICEBOX_PORT"] = "9999"
            self.assertEqual(voicebox_tts.get_base_url(), "http://127.0.0.1:9999")

            # 3. CLI parameter override
            self.assertEqual(voicebox_tts.get_base_url("http://localhost:5000/"), "http://localhost:5000")
        finally:
            if orig_env is not None:
                os.environ["VOICEBOX_PORT"] = orig_env
            else:
                os.environ.pop("VOICEBOX_PORT", None)

    def test_split_sentences(self):
        text = "Hello world. This is sentence two! Is this three?"
        chunks = voicebox_tts._split_sentences(text, max_chars=100)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], "Hello world. This is sentence two! Is this three?")

        # Small max_chars forces splitting
        chunks_small = voicebox_tts._split_sentences(text, max_chars=20)
        self.assertGreaterEqual(len(chunks_small), 3)

        # Exceptionally long sentence hard splits on words
        long_sentence = "word " * 150
        chunks_long = voicebox_tts._split_sentences(long_sentence, max_chars=50)
        for c in chunks_long:
            self.assertLessEqual(len(c), 50)

    def test_wav_build_and_parse(self):
        sample_rate = 24000
        channels = 1
        bits = 16
        pcm1 = b"\x00\x00" * 100
        pcm2 = b"\x01\x00" * 100

        wav_data = voicebox_tts._build_wav([pcm1, pcm2], sample_rate, channels, bits)
        
        parsed_sr, parsed_nc, parsed_bits, offset, data_len = voicebox_tts._parse_wav_header(wav_data)
        self.assertEqual(parsed_sr, sample_rate)
        self.assertEqual(parsed_nc, channels)
        self.assertEqual(parsed_bits, bits)
        self.assertEqual(data_len, len(pcm1) + len(pcm2))
        self.assertEqual(wav_data[offset:offset+data_len], pcm1 + pcm2)

    def test_parse_invalid_wav(self):
        with self.assertRaises(ValueError):
            voicebox_tts._parse_wav_header(b"NOTAWAVDATA")


class MockVoiceboxHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # suppress HTTP logging output during tests

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode())
        elif self.path == "/settings/active-voice":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"voice_id": "test-voice-123"}).encode())
        elif self.path == "/profiles/test-voice-123":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"id": "test-voice-123", "default_engine": "kokoro"}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/generate/stream":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            payload = json.loads(body.decode())

            # Return a valid WAV chunk for the test payload
            sample_pcm = b"\x05\x00" * 50
            wav_bytes = voicebox_tts._build_wav([sample_pcm], 22050, 1, 16)

            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.end_headers()
            self.wfile.write(wav_bytes)
        else:
            self.send_response(404)
            self.end_headers()


class TestEndToEndVoiceboxTTS(unittest.TestCase):

    def test_e2e_voicebox_tts_main(self):
        server = HTTPServer(("127.0.0.1", 0), MockVoiceboxHandler)
        server_port = server.server_port
        server_thread = threading.Thread(target=server.serve_forever)
        server_thread.daemon = True
        server_thread.start()

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            text_file = tmp_path / "input.txt"
            out_file = tmp_path / "output.wav"
            text_file.write_text("Hello from Hermes Voicebox integration test!", encoding="utf-8")

            test_args = [
                "voicebox_tts.py",
                "--text-file", str(text_file),
                "--out", str(out_file),
                "--base-url", f"http://127.0.0.1:{server_port}",
                "--voice", "default"
            ]
            orig_argv = sys.argv
            sys.argv = test_args
            try:
                voicebox_tts.main()
            finally:
                sys.argv = orig_argv
                server.shutdown()
                server.server_close()

            self.assertTrue(out_file.exists())
            out_bytes = out_file.read_bytes()
            sr, nc, bits, offset, data_len = voicebox_tts._parse_wav_header(out_bytes)
            self.assertEqual(sr, 22050)
            self.assertEqual(nc, 1)
            self.assertEqual(bits, 16)
            self.assertGreater(data_len, 0)


if __name__ == "__main__":
    unittest.main()
