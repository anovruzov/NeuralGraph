//! Python's `re` word-character class (`\w` on `str` patterns), for the places where a Rust regex `\w` would
//! give a different answer on real conversation text.
//!
//! Python's `\w` is `str.isalnum()` plus `_`: letters (`L*`) and characters with a numeric value (`Nd`, `Nl`,
//! `No`, CJK numerals). Rust's `\w` is UTS #18 "word": `Alphabetic`, every mark (`M`), `Nd`, every connector
//! punctuation (`Pc`) and `Join_Control`. The two disagree on characters LoCoMo actually contains: emoji
//! sequences carry U+200D ZERO WIDTH JOINER and U+FE0F VARIATION SELECTOR-16, which are word characters to Rust
//! and not to Python, so `re.sub(r"[^\w]", "", "Yoga🧘‍♀️")` keeps two invisible characters in Rust unless the
//! class below is used. `tests/dialogue_linker.rs` checks [`py_is_word_char`] and [`PY_WORD_CLASS`] against
//! Python's own `\w` for every code point assigned in Python 3.11's Unicode (14.0).

use std::sync::LazyLock;

/// The body of a Rust regex character class equal to Python's `\w`: use as `[{PY_WORD_CLASS}]`, negated as
/// `[^{PY_WORD_CLASS}]`. `\w` plus `No`, minus marks, joiners, non-underscore connectors and the circled /
/// squared Latin letters (symbols that Unicode lists as `Other_Alphabetic` but Python does not call alphabetic).
pub const PY_WORD_CLASS: &str = r"[\w\p{No}]--[[\p{M}\p{Join_Control}\p{Pc}\x{24B6}-\x{24E9}\x{1F130}-\x{1F149}\x{1F150}-\x{1F169}\x{1F170}-\x{1F189}]--_]";

/// `[^\w]` as Python reads it.
pub static PY_NON_WORD: LazyLock<regex::Regex> =
    LazyLock::new(|| regex::Regex::new(&format!("[^{PY_WORD_CLASS}]")).expect("valid class"));

/// Circled and squared Latin capital/small letters: `So` characters with `Other_Alphabetic`.
fn is_alphabetic_symbol(c: char) -> bool {
    matches!(c as u32, 0x24B6..=0x24E9 | 0x1F130..=0x1F149 | 0x1F150..=0x1F169 | 0x1F170..=0x1F189)
}

/// `re.match(r"\w", c)` in Python: `c.isalnum() or c == "_"`.
pub fn py_is_word_char(c: char) -> bool {
    if c == '_' {
        return true;
    }
    c.is_alphanumeric() && !unicode_normalization::char::is_combining_mark(c) && !is_alphabetic_symbol(c)
}

/// `re.sub(r"[^\w]", "", s)`.
pub fn strip_non_word(s: &str) -> String {
    s.chars().filter(|c| py_is_word_char(*c)).collect()
}

/// Maximal runs of Python word characters, in order.
pub fn word_runs(s: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut start: Option<usize> = None;
    for (i, c) in s.char_indices() {
        match (py_is_word_char(c), start) {
            (true, None) => start = Some(i),
            (false, Some(st)) => {
                out.push(&s[st..i]);
                start = None;
            }
            _ => {}
        }
    }
    if let Some(st) = start {
        out.push(&s[st..]);
    }
    out
}

/// `re.findall(r"\b<first><rest>{min_rest,}\b", s)` where `first` and `rest` only accept word characters (the
/// `\b[A-Z][a-z]+\b` family). Such a match is always a whole run of word characters, so this is exact under
/// Python's `\w` where a Rust regex `\b` is not (it treats joiners and marks as word characters).
pub fn findall_whole_words(
    s: &str,
    first: impl Fn(char) -> bool,
    rest: impl Fn(char) -> bool,
    min_rest: usize,
) -> Vec<&str> {
    word_runs(s)
        .into_iter()
        .filter(|w| {
            let mut cs = w.chars();
            cs.next().is_some_and(&first) && {
                let tail: Vec<char> = cs.collect();
                tail.len() >= min_rest && tail.iter().all(|c| rest(*c))
            }
        })
        .collect()
}

/// Python's `\b` as lookarounds over its own `\w` (fancy-regex syntax).
fn py_boundary() -> String {
    let w = format!("[{PY_WORD_CLASS}]");
    format!("(?:(?<={w})(?!{w})|(?<!{w})(?={w}))")
}

/// Translate a Python `re` pattern (`str` pattern, no `re.MULTILINE`) into an equivalent fancy-regex pattern:
/// `\w`, `\W`, `\b`, `\B`, `\s`, `\S` take Python's definitions (inside character classes too), and `$`
/// also matches before a final newline as Python's does. Everything else is the shared syntax.
pub fn translate(py: &str) -> String {
    let w = format!("[{PY_WORD_CLASS}]");
    let mut out = String::with_capacity(py.len() * 2);
    let mut in_class = false;
    let mut chars = py.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '\\' => {
                let Some(e) = chars.next() else {
                    out.push('\\');
                    break;
                };
                match (e, in_class) {
                    ('w', _) => out.push_str(&w),
                    ('W', false) => out.push_str(&format!("[^{PY_WORD_CLASS}]")),
                    ('s', false) => out.push_str(r"[\s\x1c-\x1f]"),
                    ('s', true) => out.push_str(r"\s\x1c-\x1f"),
                    ('S', false) => out.push_str(r"[^\s\x1c-\x1f]"),
                    ('b', false) => out.push_str(&py_boundary()),
                    ('B', false) => out.push_str(&format!("(?!{})", py_boundary())),
                    ('W' | 'S', true) => panic!("pyre::translate: \\{e} inside a class is not supported"),
                    _ => {
                        out.push('\\');
                        out.push(e);
                    }
                }
            }
            '[' if !in_class => {
                in_class = true;
                out.push('[');
                // a leading `^` and a leading `]` belong to the class syntax
                if chars.peek() == Some(&'^') {
                    out.push(chars.next().unwrap());
                }
                if chars.peek() == Some(&']') {
                    chars.next();
                    out.push_str("\\]");
                }
            }
            '[' => out.push_str("\\["),
            ']' if in_class => {
                in_class = false;
                out.push(']');
            }
            '$' if !in_class => out.push_str(r"(?=\n?\z)"),
            c => out.push(c),
        }
    }
    out
}

/// Compile a Python `re` pattern with Python's semantics (see [`translate`]); `ignore_case` is `re.IGNORECASE`.
pub fn compile(py: &str, ignore_case: bool) -> fancy_regex::Regex {
    let prefix = if ignore_case { "(?i)" } else { "" };
    let pattern = format!("{prefix}{}", translate(py));
    fancy_regex::Regex::new(&pattern).unwrap_or_else(|e| panic!("pyre::compile({py:?}): {e}"))
}

/// `re.findall(pattern, s)`: whole matches, or group 1 when the pattern has exactly one group, or every group
/// (`None` as "") when it has more.
pub fn findall(re: &fancy_regex::Regex, s: &str) -> Vec<Vec<String>> {
    let groups = re.captures_len() - 1;
    let mut out = Vec::new();
    for caps in re.captures_iter(s) {
        let caps = caps.expect("regex backtrack limit");
        out.push(match groups {
            0 => vec![caps.get(0).unwrap().as_str().to_string()],
            _ => (1..=groups).map(|i| caps.get(i).map_or(String::new(), |m| m.as_str().to_string())).collect(),
        });
    }
    out
}

/// `re.findall` of a pattern with at most one group, as strings.
pub fn findall1(re: &fancy_regex::Regex, s: &str) -> Vec<String> {
    findall(re, s).into_iter().map(|mut g| g.swap_remove(0)).collect()
}

/// `bool(re.search(pattern, s))`
pub fn search(re: &fancy_regex::Regex, s: &str) -> bool {
    re.is_match(s).expect("regex backtrack limit")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn emoji_joiners_are_not_word_characters() {
        assert_eq!(strip_non_word("Yoga\u{1F9D8}\u{200D}\u{2640}\u{FE0F}!"), "Yoga");
        assert_eq!(PY_NON_WORD.replace_all("Yoga\u{1F9D8}\u{200D}\u{2640}\u{FE0F}!", ""), "Yoga");
        assert!(py_is_word_char('_') && py_is_word_char('\u{00B2}') && !py_is_word_char('\u{203F}'));
    }

    #[test]
    fn translated_patterns_follow_python() {
        let re = compile(r"\b[a-z]{3,}\b", false);
        assert_eq!(findall1(&re, "fun\u{200D} yes_no abc\u{00B2} hello\u{FE0F}"), vec!["fun", "hello"]);
        let tail = compile(r"\b(?:too|as well)\b$", true);
        assert!(search(&tail, "me TOO\n") && !search(&tail, "metoo"));
        let cls = compile(r"[^\w\s\-]", false);
        assert_eq!(cls.replace_all("a-b c!\u{200D}", ""), "a-b c");
    }

    #[test]
    fn whole_words() {
        let caps = |t| findall_whole_words(t, |c: char| c.is_ascii_uppercase(), |c: char| c.is_ascii_alphabetic(), 1);
        assert_eq!(caps("Hi Bob, Café x2 Ann\u{200D} I OK_ Zed"), vec!["Hi", "Bob", "Ann", "Zed"]);
    }
}
