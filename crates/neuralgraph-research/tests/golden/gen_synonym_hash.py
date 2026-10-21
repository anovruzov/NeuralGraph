#!/usr/bin/env python3
"""Fixtures for ``retrieval/synonym_hash.py``: a digest of the whole loaded thesaurus table (every word and its
synonym set), and the extended synonyms of every word of every LoCoMo question plus edge cases. Sets are stored
sorted (Python's set order was hash-dependent). Writes ``synonym_hash.json``."""
from __future__ import annotations

import hashlib
import json
import re

import _reference as R

import NeuralGraph.research.retrieval.synonym_hash as S


def table_digest(table) -> str:
    text = "\n".join(k + "\t" + "|".join(sorted(table[k])) for k in sorted(table))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main():
    table = S._load_thesaurus()
    data = json.load(open(R.DATASETS / "locomo10.json"))
    words: list[str] = []
    for conv in data:
        for qa in conv["qa"]:
            for w in re.findall(r"\b[a-z]{3,}\b", qa["question"].lower()):
                if w not in words:
                    words.append(w)
    words += ["Animal", "PETS", "big_cat", "big cat", "15 may organization", "zzzunknown", "", "a", "Console",
              "gave", "Rejected", "run", "happy"]
    extended = {w: sorted(S.get_synonyms_extended(w)) for w in words}
    plain = {w: sorted(S.get_synonyms(w)) for w in words[:200]}
    sets = [["camping", "Hike"], ["pet", "letter", "unknownword"], [], ["Gift", "gift"]]
    expanded = [{"words": ws, "plain": sorted(S.expand_with_synonyms(set(ws))),
                 "extended": sorted(S.expand_with_synonyms_extended(set(ws)))} for ws in sets]
    R.dump("synonym_hash.json", {
        "table_size": len(table),
        "table_sha256": table_digest(table),
        "domain": {k: sorted(v) for k, v in S.DOMAIN_SYNONYMS.items()},
        "extended": extended,
        "plain": plain,
        "expanded": expanded,
    })


main()
