"""Lemma fallback for translation dictionary lookups."""

from __future__ import annotations

import time
import unittest

from services.translation_extras_service import (
    TranslationExtrasService,
    dictionary_lookup_forms,
    hyphenated_particle_form,
    lemmatize_dictionary_word,
)


class DictionaryLemmaTests(unittest.TestCase):
    def test_irregular_forms_map_to_safe_lemmas(self) -> None:
        self.assertEqual(lemmatize_dictionary_word("went", "en"), "go")
        self.assertEqual(lemmatize_dictionary_word("mice", "en"), "mouse")
        self.assertEqual(lemmatize_dictionary_word("happiest", "en"), "happy")
        self.assertEqual(lemmatize_dictionary_word("passersby", "en"), "passerby")
        self.assertEqual(lemmatize_dictionary_word("xyzzy", "en"), "xyzzy")
        self.assertNotIn("passersb", dictionary_lookup_forms("passersby", "en"))

    def test_lookup_forms_include_lemma_and_hyphen_particle(self) -> None:
        self.assertEqual(dictionary_lookup_forms("went", "en"), ["went", "go"])
        self.assertEqual(
            dictionary_lookup_forms("passersby", "en"),
            ["passersby", "passerby", "passer-by"],
        )
        self.assertEqual(hyphenated_particle_form("passerby"), "passer-by")
        self.assertEqual(dictionary_lookup_forms("xyzzy", "en"), ["xyzzy"])

    def test_lemmatize_is_fast_after_warmup(self) -> None:
        lemmatize_dictionary_word("went", "en")
        started = time.perf_counter()
        for _ in range(200):
            lemmatize_dictionary_word("happiest", "en")
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.assertLess(elapsed_ms, 50)

    def test_lookup_retries_lemma_then_hyphen_and_skips_typos(self) -> None:
        seen: list[str] = []
        lexicon = {
            "go": "to move",
            "mouse": "a small rodent",
            "happy": "feeling pleasure",
            "passer-by": "a person who happens to pass by",
        }

        def fetch(word: str, language: object = "en") -> tuple[int, object]:
            seen.append(str(word))
            token = str(word).strip().lower()
            if token not in lexicon:
                return 404, {"title": "No Definitions Found"}
            return 200, [
                {
                    "word": token,
                    "meanings": [
                        {
                            "partOfSpeech": "noun",
                            "definitions": [{"definition": lexicon[token]}],
                        }
                    ],
                }
            ]

        service = TranslationExtrasService(
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            gemini_generate=lambda *_args, **_kwargs: "",
            dictionary_fetch=fetch,
        )
        went = service.lookup_word(None, "went", "en")
        self.assertTrue(went.get("found"))
        self.assertEqual(went.get("looked_up_as"), "go")
        self.assertEqual(went.get("queried_word"), "went")

        mice = service.lookup_word(None, "mice", "en")
        self.assertEqual(mice.get("looked_up_as"), "mouse")

        happiest = service.lookup_word(None, "happiest", "en")
        self.assertEqual(happiest.get("looked_up_as"), "happy")

        passersby = service.lookup_word(None, "passersby", "en")
        self.assertTrue(passersby.get("found"))
        self.assertEqual(passersby.get("looked_up_as"), "passer-by")
        self.assertEqual(
            [item for item in seen if item.startswith("passer")],
            ["passersby", "passerby", "passer-by"],
        )

        missing = service.lookup_word(None, "xyzzy", "en")
        self.assertFalse(missing.get("found"))
        self.assertEqual(missing.get("status"), "not_found")
        self.assertEqual(missing.get("word"), "xyzzy")
        self.assertNotIn("passersb", seen)


if __name__ == "__main__":
    unittest.main()
