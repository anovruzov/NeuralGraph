"""Thesaurus, spelling detector and name extraction used by the memory server."""

from __future__ import annotations

import unittest

from NeuralGraph.mcp import lexicon as L


class LexiconTests(unittest.TestCase):
    def test_dictionary_includes_headwords_without_synonyms(self):
        for w in ("deployment", "database", "editor", "quick"):
            self.assertIn(w, L.dictionary())
        self.assertNotIn("posgres", L.dictionary())

    def test_synonyms_curated_first_then_wordnet(self):
        self.assertEqual(L.synonyms("editor")[:1], ("ide",))
        self.assertIn("release", L.synonyms("deploy"))
        self.assertIn("deploy", L.synonyms("ship"))          # bidirectional
        self.assertIn("speedy", L.synonyms("quick"))         # WordNet
        self.assertEqual(L.synonyms("zzzzqqq"), ())

    def test_expand_only_into_words_the_corpus_contains(self):
        self.assertEqual(L.expand(["deploy"], {"ship", "postgres"}), {"deploy": ("ship",)})
        self.assertEqual(L.expand(["deploy"], {"postgres"}), {})

    def test_correct_prefers_corpus_then_dictionary_and_protects_names(self):
        corpus = {"postgres": 3, "neovim": 2, "reranker": 2}
        self.assertEqual(L.correct("posgres", corpus), ("postgres", True))
        self.assertEqual(L.correct("rerankr", corpus), ("reranker", True))
        self.assertEqual(L.correct("deploymnt", corpus), ("deployment", True))   # dictionary fallback
        self.assertEqual(L.correct("Nurman", corpus), ("Nurman", False))         # capitalised: a name
        self.assertEqual(L.correct("nurman", corpus, protected={"nurman"}), ("nurman", False))
        self.assertEqual(L.correct("2026", corpus), ("2026", False))
        self.assertEqual(L.correct("the", corpus), ("the", False))
        self.assertEqual(L.correct("postgres", corpus), ("postgres", False))
        corrected, changes = L.correct_tokens(["posgres", "on", "Nurman"], corpus)
        self.assertEqual(corrected, ["postgres", "on", "nurman"])
        self.assertEqual(changes, {"posgres": "postgres"})

    def test_names_runs_possessives_handles_and_unknown_words(self):
        names = L.names("Nurman's LM Studio runs on a Mac mini. Ping @anovruzov. The build runs in New York.")
        self.assertIn("nurman", names)
        self.assertIn("lm studio", names)
        self.assertIn("new york", names)
        self.assertIn("anovruzov", names)
        self.assertNotIn("the", names)
        self.assertNotIn("ping", names)

    def test_sentence_initial_dictionary_words_are_candidates_not_names(self):
        self.assertNotIn("ali", L.names("Ali prefers dark mode."))
        self.assertEqual(L.sentence_initial_candidates("Ali prefers dark mode."), ("ali",))
        self.assertIn("ali", L.names("Everyone agrees Ali prefers dark mode."))


if __name__ == "__main__":
    unittest.main()
