"""Typewriter typing-sound hooks: IME playback + AudioContext resume."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TypewriterSoundContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        cls.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        cls.locales = {
            lang: json.loads((ROOT / "web" / "locales" / f"{lang}.json").read_text(encoding="utf-8"))
            for lang in ("ko", "en", "es")
        }

    def test_sound_plays_on_ime_input_and_resumes_audio(self) -> None:
        self.assertIn("function maybePlayTypewriterSound(", self.js)
        self.assertIn("function typewriterInputKind(", self.js)
        self.assertIn("function playTypewriterClick(", self.js)
        kind_fn = self.js.split("function typewriterClickKind(", 1)[1].split(
            "function typewriterInputKind(", 1
        )[0]
        self.assertIn("event.keyCode === 229", kind_fn)
        self.assertIn('key === "Process"', kind_fn)
        self.assertIn('key === "Backspace" || key === "Delete"', kind_fn)
        self.assertIn("typewriterLastKeyWasDelete = true", kind_fn)
        input_fn = self.js.split("function typewriterInputKind(", 1)[1].split(
            "function playTypewriterClick(", 1
        )[0]
        self.assertIn('type.startsWith("delete")', input_fn)
        self.assertIn('if (type.startsWith("delete")) return "";', input_fn)
        play_gate = self.js.split("function maybePlayTypewriterSound(", 1)[1].split(
            "function setTypewriterSound(", 1
        )[0]
        self.assertIn("typewriterLastKeyWasDelete", play_gate)
        self.assertIn('kind === "backspace"', play_gate)
        self.assertIn("function playTypewriterSample(", self.js)
        self.assertIn('TYPEWRITER_SAMPLE_URL = "/sounds/typewriter-typing.wav"', self.js)
        self.assertTrue((ROOT / "web" / "sounds" / "typewriter-typing.wav").is_file())
        play_fn = self.js.split("function playTypewriterClick(", 1)[1].split(
            "function maybePlayTypewriterSound(", 1
        )[0]
        self.assertIn('ctx.state === "suspended"', play_fn)
        self.assertIn("loadTypewriterSample()", play_fn)
        self.assertIn("playTypewriterSample(kind)", play_fn)
        setup = self.js.split("function setupTypewriterMode(", 1)[1].split(
            "function scheduleTypewriterSync(", 1
        )[0]
        self.assertIn('event.type === "keydown" || event.type === "input"', setup)
        self.assertIn('document.addEventListener("compositionupdate"', setup)
        extras = self.js.split("function typewriterSoundContextExtras(", 1)[1].split(
            "function isTypewriterSoundTarget(", 1
        )[0]
        self.assertIn("app.타이핑_사운드", extras)
        self.assertIn('id="typewriterModeButton"', self.html)
        for locale in self.locales.values():
            self.assertIn("app.타이핑_사운드", locale)
            self.assertIn("app.타자기_모드를_켜면_타이핑_소리가_나요", locale)
        self.assertEqual(self.locales["ko"]["app.타이핑_사운드"], "타이핑 사운드")
