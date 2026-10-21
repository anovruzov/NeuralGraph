#!/usr/bin/env python3
"""Fixtures for ``NeuralGraph/temporal_utils.py`` (everything the product crate did not already pin): date tokens,
explicit and duration dates, temporal fields, preprocessing, query expansion and the query-mode router.

Inputs are every LoCoMo message with its session date (the harnesses' ``flatten_messages`` order), every LoCoMo
question, and hand-written edge cases over dates chosen to hit weekday, month, year and leap-year boundaries.
Per-message outputs are stored compactly: the date-only part of the tokens once per distinct session date, and
per message only what depends on the text. Writes ``temporal_utils.json``."""
from __future__ import annotations

import json
from datetime import datetime

import _reference as R

import NeuralGraph.temporal_utils as T


def guard(fn, *args):
    """Call ``fn``; a raised exception becomes ``{"error": "<ExceptionType>"}`` (the Rust side returns Err)."""
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 - any Python exception is an observable outcome here
        return {"error": type(exc).__name__}


def flatten_messages(conv):
    conversation = conv.get("conversation", conv)
    messages, i = [], 1
    while f"session_{i}" in conversation:
        dt = conversation.get(f"session_{i}_date_time", "")
        for msg in conversation[f"session_{i}"]:
            messages.append({"speaker": msg.get("speaker", "Unknown"), "text": msg.get("text", ""), "datetime": dt})
        i += 1
    return messages


def compact_fields(text, date):
    """``generate_temporal_fields`` minus the date-only token part (stored once per date)."""
    f = guard(T.generate_temporal_fields, text, date)
    if "error" in f:
        return f
    out = {}
    base = 10 if date else 0
    if f["temporal_tokens"][base:]:
        out["rel"] = f["temporal_tokens"][base:]
    if f["resolved_content"] != text:
        out["rc"] = f["resolved_content"]
    if f["resolved_dates"]:
        out["rds"] = f["resolved_dates"]
    if f["explicit_date"] is not None:
        out["ex"] = f["explicit_date"]
    if f["resolved_date_source"] != ("message" if date else None):
        out["rd"] = [f["resolved_date"], f["resolved_date_source"]]
    extra = {k: v for k, v in f.items() if k not in {"resolved_date", "temporal_tokens", "temporal_metadata",
                                                       "resolved_dates", "resolved_content", "explicit_date",
                                                       "resolved_date_source"}}
    if extra:
        out["dur"] = extra
    return out


def iso(d):
    return d.isoformat() if d else None


EDGE_TEXTS = [
    "", "I went camping yesterday and last week too", "since 2016 I've been painting", "since June 2020",
    "for seven years now", "I've been running for 3 months", "13 August 2023 was great", "August 13, 2023",
    "on 2023-08-13 we met", "2023/02/30 is not a date but 5 May is", "see you on 31 June", "We met on 31 September",
    "I have 2 dogs and on 13 August I adopted a cat", "it was 3 days ago", "two days ago at the weekend",
    "seven days ago", "last month and next month", "ten years ago", "this weekend", "It's been 12 years now",
    "for twelve years", "for many years", "for one month", "since march 2021, for five years",
    "May 5", "5 may", "Mayday 3", "in 2099-01-01", "at 3 pm on 5 May", "yesterday last week last month weekend 4 days ago",
    "Since 2019 and since July 2022", "for 0 years", "99999999999999999999 days ago", "maybe in May",
    "Last Friday, this Sat. and next tues", "earlier today, tonight and tomorrow",
]
EDGE_DATES = [None, datetime(2023, 5, 8, 13, 56), datetime(2024, 1, 1), datetime(2023, 12, 31, 23, 59),
              datetime(2024, 3, 1), datetime(2023, 1, 7), datetime(2023, 7, 9), datetime(2022, 2, 28),
              datetime(1, 1, 1), datetime(9999, 12, 31)]

QUERY_EDGES = [
    "When did X happen in May 2023?", "What happened last week and yesterday?", "Summer or autumn 2022 or 2019?",
    "Is it maybe on Monday?", "", "  When  ", "What was the poetry reading about?", "How many years ago?",
    "How many pets does she have?", "What LGBTQ+ events has Caroline attended?", "Who isn't coming?",
    "Would Melanie enjoy hiking?", "Why did he leave?", "The dog", "Describe her personality",
]


def main():
    data = json.load(open(R.DATASETS / "locomo10.json"))
    dates = {}
    messages = []
    for ci, conv in enumerate(data):
        for mi, m in enumerate(flatten_messages(conv)):
            if m["datetime"] not in dates:
                parsed = T.parse_datetime_flexible(m["datetime"])
                tokens = T.generate_temporal_tokens(parsed, "")
                dates[m["datetime"]] = {"parsed": iso(parsed), "tokens": tokens}
            date = T.parse_datetime_flexible(m["datetime"])
            rec = compact_fields(m["text"], date)
            rec["mode"] = T.infer_query_mode(m["text"])
            if rec != {"mode": rec["mode"]}:
                rec["c"], rec["i"] = ci, mi
                messages.append(rec)
            else:
                messages.append(rec["mode"])

    questions = []
    for conv in data:
        for qa in conv["qa"]:
            q = qa["question"]
            exp = T.expand_temporal_query(q)
            questions.append([T.infer_query_mode(q), exp[len(q):] if exp.startswith(q) else {"full": exp}])

    edges = []
    for d in EDGE_DATES:
        for t in EDGE_TEXTS:
            edges.append({
                "date": iso(d), "text": t,
                "tokens": guard(T.generate_temporal_tokens, d, t),
                "explicit": guard(T.extract_explicit_date, t, d),
                "duration": guard(T.extract_duration_metadata, t, d),
                "fields": guard(T.generate_temporal_fields, t, d),
                "pre": guard(T.preprocess_message_for_indexing, t, None, d),
            })
    pre_from_string = [{"text": t, "dt": s, "out": guard(T.preprocess_message_for_indexing, t, s)}
                       for t in ["went out yesterday", "last weekend was fun"]
                       for s in [None, "", "1:56 pm on 8 May, 2023", "31 February 2023", "2023-05-08", "garbage"]]
    number_tokens = {t: T._parse_number_token(t) for t in ["", "3", " 7 ", "Twelve", "thirteen", "007", "x1"]}
    queries = [{"q": q, "mode": T.infer_query_mode(q), "expanded": T.expand_temporal_query(q)} for q in QUERY_EDGES]

    R.dump("temporal_utils.json", {
        "dates": dates,
        "messages": messages,
        "questions": questions,
        "edges": edges,
        "pre_from_string": pre_from_string,
        "number_tokens": number_tokens,
        "queries": queries,
        "constants": {"MONTH_NAMES_REV": T.MONTH_NAMES_REV, "DAY_NAMES_REV": T.DAY_NAMES_REV,
                      "DAY_ABBREVIATIONS": T.DAY_ABBREVIATIONS, "NUMBER_WORDS": T.NUMBER_WORDS,
                      "QUERY_MODE_MARKERS": {m: {c: sorted(p) for c, p in cats.items()}
                                             for m, cats in T.QUERY_MODE_MARKERS.items()}},
    })


main()
