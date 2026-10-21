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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn emoji_joiners_are_not_word_characters() {
        assert_eq!(strip_non_word("Yoga\u{1F9D8}\u{200D}\u{2640}\u{FE0F}!"), "Yoga");
        assert_eq!(PY_NON_WORD.replace_all("Yoga\u{1F9D8}\u{200D}\u{2640}\u{FE0F}!", ""), "Yoga");
        assert!(py_is_word_char('_') && py_is_word_char('\u{00B2}') && !py_is_word_char('\u{203F}'));
    }
}
