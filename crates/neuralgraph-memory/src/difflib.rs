//! Port of the parts of Python's `difflib` the entity registry uses for fuzzy alias matching
//! (`get_close_matches` over `SequenceMatcher` ratios). Ported line for line so that the same mention resolves
//! to the same entity key as before — a different key would split one person into two entities.

use std::collections::HashMap;

/// `SequenceMatcher(None, a, b)` with `autojunk=True`, specialised to strings (sequences of code points).
pub struct SequenceMatcher {
    a: Vec<char>,
    b: Vec<char>,
    b2j: HashMap<char, Vec<usize>>,
    fullbcount: Option<HashMap<char, i64>>,
}

impl SequenceMatcher {
    pub fn new(a: &str, b: &str) -> Self {
        let mut s = SequenceMatcher { a: Vec::new(), b: Vec::new(), b2j: HashMap::new(), fullbcount: None };
        s.set_seq2(b);
        s.set_seq1(a);
        s
    }

    pub fn set_seq1(&mut self, a: &str) {
        self.a = a.chars().collect();
    }

    pub fn set_seq2(&mut self, b: &str) {
        self.b = b.chars().collect();
        self.fullbcount = None;
        self.chain_b();
    }

    fn chain_b(&mut self) {
        let mut b2j: HashMap<char, Vec<usize>> = HashMap::new();
        for (i, elt) in self.b.iter().enumerate() {
            b2j.entry(*elt).or_default().push(i);
        }
        // isjunk is None: no junk. autojunk: drop "popular" elements of long sequences.
        let n = self.b.len();
        if n >= 200 {
            let ntest = n / 100 + 1;
            b2j.retain(|_, idxs| idxs.len() <= ntest);
        }
        self.b2j = b2j;
    }

    /// Longest matching block in `a[alo:ahi]` and `b[blo:bhi]` as `(i, j, k)`.
    fn find_longest_match(&self, alo: usize, ahi: usize, blo: usize, bhi: usize) -> (usize, usize, usize) {
        let (a, b) = (&self.a, &self.b);
        let (mut besti, mut bestj, mut bestsize) = (alo, blo, 0usize);
        let mut j2len: HashMap<usize, usize> = HashMap::new();
        for (i, ai) in a.iter().enumerate().take(ahi).skip(alo) {
            let mut newj2len: HashMap<usize, usize> = HashMap::new();
            if let Some(js) = self.b2j.get(ai) {
                for &j in js {
                    if j < blo {
                        continue;
                    }
                    if j >= bhi {
                        break;
                    }
                    let k = if j > 0 { j2len.get(&(j - 1)).copied().unwrap_or(0) } else { 0 } + 1;
                    newj2len.insert(j, k);
                    if k > bestsize {
                        besti = i + 1 - k;
                        bestj = j + 1 - k;
                        bestsize = k;
                    }
                }
            }
            j2len = newj2len;
        }
        // Extend with non-junk equal elements on both sides (no junk here, but popular elements were removed
        // from b2j and are recovered this way, exactly as in CPython).
        while besti > alo && bestj > blo && a[besti - 1] == b[bestj - 1] {
            besti -= 1;
            bestj -= 1;
            bestsize += 1;
        }
        while besti + bestsize < ahi && bestj + bestsize < bhi && a[besti + bestsize] == b[bestj + bestsize] {
            bestsize += 1;
        }
        (besti, bestj, bestsize)
    }

    fn matching_total(&self) -> usize {
        let (la, lb) = (self.a.len(), self.b.len());
        let mut queue = vec![(0usize, la, 0usize, lb)];
        let mut total = 0;
        while let Some((alo, ahi, blo, bhi)) = queue.pop() {
            let (i, j, k) = self.find_longest_match(alo, ahi, blo, bhi);
            if k > 0 {
                total += k;
                if alo < i && blo < j {
                    queue.push((alo, i, blo, j));
                }
                if i + k < ahi && j + k < bhi {
                    queue.push((i + k, ahi, j + k, bhi));
                }
            }
        }
        total
    }

    fn calc_ratio(matches: usize, length: usize) -> f64 {
        if length > 0 {
            2.0 * matches as f64 / length as f64
        } else {
            1.0
        }
    }

    pub fn ratio(&self) -> f64 {
        Self::calc_ratio(self.matching_total(), self.a.len() + self.b.len())
    }

    pub fn quick_ratio(&mut self) -> f64 {
        if self.fullbcount.is_none() {
            let mut counts: HashMap<char, i64> = HashMap::new();
            for c in &self.b {
                *counts.entry(*c).or_insert(0) += 1;
            }
            self.fullbcount = Some(counts);
        }
        let full = self.fullbcount.as_ref().expect("computed above");
        let mut avail: HashMap<char, i64> = HashMap::new();
        let mut matches = 0;
        for c in &self.a {
            let numb = match avail.get(c) {
                Some(v) => *v,
                None => full.get(c).copied().unwrap_or(0),
            };
            avail.insert(*c, numb - 1);
            if numb > 0 {
                matches += 1;
            }
        }
        Self::calc_ratio(matches, self.a.len() + self.b.len())
    }

    pub fn real_quick_ratio(&self) -> f64 {
        let (la, lb) = (self.a.len(), self.b.len());
        Self::calc_ratio(la.min(lb), la + lb)
    }
}

/// `difflib.get_close_matches(word, possibilities, n, cutoff)`: best `n` candidates scoring at least `cutoff`,
/// highest `(ratio, candidate)` first.
pub fn get_close_matches<'a, I>(word: &str, possibilities: I, n: usize, cutoff: f64) -> Vec<String>
where
    I: IntoIterator<Item = &'a str>,
{
    let mut s = SequenceMatcher::new("", word);
    let mut result: Vec<(f64, String)> = Vec::new();
    for x in possibilities {
        s.set_seq1(x);
        if s.real_quick_ratio() >= cutoff && s.quick_ratio() >= cutoff {
            let r = s.ratio();
            if r >= cutoff {
                result.push((r, x.to_string()));
            }
        }
    }
    // heapq.nlargest compares (ratio, string) tuples
    result.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal).then_with(|| b.1.cmp(&a.1)));
    result.into_iter().take(n).map(|(_, x)| x).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ratios_match_python() {
        assert_eq!(SequenceMatcher::new("abcd", "bcde").ratio(), 0.75);
        assert_eq!(get_close_matches("berlín", ["berlin", "tokyo"], 1, 0.6), vec!["berlin".to_string()]);
        assert_eq!(
            get_close_matches("appel", ["ape", "apple", "peach", "puppy"], 3, 0.6),
            vec!["apple".to_string(), "ape".to_string()]
        );
    }
}
