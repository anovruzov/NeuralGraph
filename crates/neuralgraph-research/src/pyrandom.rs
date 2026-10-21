//! Python's `random.Random` and numpy's legacy `np.random` global state, bit for bit.
//!
//! Two pieces of the research code are seeded and must replay the same draws: the coordination fixture
//! (`random.Random(seed)` with `choice`) and the LSH projections (`np.random.seed(42)` then `np.random.randn`).
//! The LongMemEval harness draws its stratified sample with `random.Random(seed).sample`/`shuffle`. Both
//! libraries sit on the same Mersenne Twister (MT19937) but seed it differently: CPython feeds the 32-bit words
//! of the integer to `init_by_array`; numpy's legacy `seed(int)` uses `init_genrand`. Normal deviates come from
//! numpy's legacy polar Box–Muller (`legacy_gauss`), which caches the second value of each pair.

const N: usize = 624;
const M: usize = 397;
const MATRIX_A: u32 = 0x9908_b0df;
const UPPER_MASK: u32 = 0x8000_0000;
const LOWER_MASK: u32 = 0x7fff_ffff;

/// The MT19937 generator as written in CPython's `_randommodule.c` and numpy's `mt19937.c`.
#[derive(Clone, Debug)]
pub struct Mt19937 {
    mt: [u32; N],
    index: usize,
}

impl Mt19937 {
    /// `init_genrand(s)`.
    pub fn from_u32(seed: u32) -> Self {
        let mut mt = [0u32; N];
        mt[0] = seed;
        for i in 1..N {
            mt[i] = 1_812_433_253u32.wrapping_mul(mt[i - 1] ^ (mt[i - 1] >> 30)).wrapping_add(i as u32);
        }
        Mt19937 { mt, index: N }
    }

    /// `init_by_array(init_key)`.
    pub fn from_key(key: &[u32]) -> Self {
        let mut g = Mt19937::from_u32(19_650_218);
        let mt = &mut g.mt;
        let key_length = key.len().max(1);
        let mut i = 1usize;
        let mut j = 0usize;
        let mut k = N.max(key_length);
        while k > 0 {
            let kj = key.get(j).copied().unwrap_or(0);
            mt[i] = (mt[i] ^ (mt[i - 1] ^ (mt[i - 1] >> 30)).wrapping_mul(1_664_525))
                .wrapping_add(kj)
                .wrapping_add(j as u32);
            i += 1;
            j += 1;
            if i >= N {
                mt[0] = mt[N - 1];
                i = 1;
            }
            if j >= key_length {
                j = 0;
            }
            k -= 1;
        }
        k = N - 1;
        while k > 0 {
            mt[i] = (mt[i] ^ (mt[i - 1] ^ (mt[i - 1] >> 30)).wrapping_mul(1_566_083_941)).wrapping_sub(i as u32);
            i += 1;
            if i >= N {
                mt[0] = mt[N - 1];
                i = 1;
            }
            k -= 1;
        }
        mt[0] = 0x8000_0000;
        g.index = N;
        g
    }

    fn generate(&mut self) {
        let mag01 = [0u32, MATRIX_A];
        let mt = &mut self.mt;
        for kk in 0..N - M {
            let y = (mt[kk] & UPPER_MASK) | (mt[kk + 1] & LOWER_MASK);
            mt[kk] = mt[kk + M] ^ (y >> 1) ^ mag01[(y & 1) as usize];
        }
        for kk in N - M..N - 1 {
            let y = (mt[kk] & UPPER_MASK) | (mt[kk + 1] & LOWER_MASK);
            mt[kk] = mt[kk + M - N] ^ (y >> 1) ^ mag01[(y & 1) as usize];
        }
        let y = (mt[N - 1] & UPPER_MASK) | (mt[0] & LOWER_MASK);
        mt[N - 1] = mt[M - 1] ^ (y >> 1) ^ mag01[(y & 1) as usize];
        self.index = 0;
    }

    /// `genrand_uint32()`.
    pub fn next_u32(&mut self) -> u32 {
        if self.index >= N {
            self.generate();
        }
        let mut y = self.mt[self.index];
        self.index += 1;
        y ^= y >> 11;
        y ^= (y << 7) & 0x9d2c_5680;
        y ^= (y << 15) & 0xefc6_0000;
        y ^= y >> 18;
        y
    }

    /// `genrand_res53()` / numpy's `legacy_double`: a double in [0, 1) with 53 random bits.
    pub fn next_f64(&mut self) -> f64 {
        let a = (self.next_u32() >> 5) as f64;
        let b = (self.next_u32() >> 6) as f64;
        (a * 67_108_864.0 + b) * (1.0 / 9_007_199_254_740_992.0)
    }
}

/// CPython's `random.Random`, for the methods the research code calls.
#[derive(Clone, Debug)]
pub struct PyRandom {
    mt: Mt19937,
}

impl PyRandom {
    /// `random.Random(seed)` for an integer seed: the absolute value's 32-bit words, least significant first.
    pub fn new(seed: i128) -> Self {
        let mut n = seed.unsigned_abs();
        let mut key = Vec::new();
        while n > 0 {
            key.push((n & 0xffff_ffff) as u32);
            n >>= 32;
        }
        if key.is_empty() {
            key.push(0);
        }
        PyRandom { mt: Mt19937::from_key(&key) }
    }

    /// `random()`.
    pub fn random(&mut self) -> f64 {
        self.mt.next_f64()
    }

    /// `getrandbits(k)` for `0 < k <= 128`.
    pub fn getrandbits(&mut self, k: u32) -> u128 {
        assert!(k > 0 && k <= 128, "getrandbits supports 1..=128 bits here");
        if k <= 32 {
            return (self.mt.next_u32() >> (32 - k)) as u128;
        }
        let mut result: u128 = 0;
        let mut remaining = k;
        let mut shift = 0;
        while remaining > 0 {
            let mut r = self.mt.next_u32();
            if remaining < 32 {
                r >>= 32 - remaining;
            }
            result |= (r as u128) << shift;
            shift += 32;
            remaining = remaining.saturating_sub(32);
        }
        result
    }

    /// `_randbelow(n)` (the getrandbits rejection sampler).
    pub fn randbelow(&mut self, n: u128) -> u128 {
        assert!(n > 0, "randbelow(0)");
        let k = 128 - n.leading_zeros();
        let mut r = self.getrandbits(k);
        while r >= n {
            r = self.getrandbits(k);
        }
        r
    }

    /// `randrange(start, stop)` with step 1.
    pub fn randrange(&mut self, start: i64, stop: i64) -> i64 {
        assert!(stop > start, "empty range for randrange");
        start + self.randbelow((stop - start) as u128) as i64
    }

    /// `randint(a, b)`.
    pub fn randint(&mut self, a: i64, b: i64) -> i64 {
        self.randrange(a, b + 1)
    }

    /// `choice(seq)`.
    pub fn choice<'a, T>(&mut self, seq: &'a [T]) -> &'a T {
        assert!(!seq.is_empty(), "Cannot choose from an empty sequence");
        &seq[self.randbelow(seq.len() as u128) as usize]
    }

    /// `shuffle(x)` in place.
    pub fn shuffle<T>(&mut self, x: &mut [T]) {
        for i in (1..x.len()).rev() {
            let j = self.randbelow((i + 1) as u128) as usize;
            x.swap(i, j);
        }
    }

    /// `sample(population, k)` (CPython 3.11 algorithm: a pool for small populations, a selected-set otherwise).
    pub fn sample<T: Clone>(&mut self, population: &[T], k: usize) -> Vec<T> {
        let n = population.len();
        assert!(k <= n, "Sample larger than population or is negative");
        let mut setsize = 21usize;
        if k > 5 {
            let exponent = ((k as f64 * 3.0).ln() / 4f64.ln()).ceil() as u32;
            setsize += 4usize.pow(exponent);
        }
        let mut result = Vec::with_capacity(k);
        if n <= setsize {
            let mut pool: Vec<T> = population.to_vec();
            for i in 0..k {
                let j = self.randbelow((n - i) as u128) as usize;
                result.push(pool[j].clone());
                pool[j] = pool[n - i - 1].clone();
            }
        } else {
            let mut selected = std::collections::HashSet::new();
            for _ in 0..k {
                let mut j = self.randbelow(n as u128) as usize;
                while selected.contains(&j) {
                    j = self.randbelow(n as u128) as usize;
                }
                selected.insert(j);
                result.push(population[j].clone());
            }
        }
        result
    }
}

/// numpy's legacy global generator (`np.random.seed` / `np.random.randn`).
#[derive(Clone, Debug)]
pub struct NumpyRandomState {
    mt: Mt19937,
    gauss: Option<f64>,
}

impl NumpyRandomState {
    /// `np.random.seed(seed)` for an integer in `0..2**32`.
    pub fn new(seed: u32) -> Self {
        NumpyRandomState { mt: Mt19937::from_u32(seed), gauss: None }
    }

    /// `np.random.random_sample()`.
    pub fn random_sample(&mut self) -> f64 {
        self.mt.next_f64()
    }

    /// One `legacy_gauss` deviate: polar Box–Muller, second value of each pair cached.
    pub fn standard_normal(&mut self) -> f64 {
        if let Some(g) = self.gauss.take() {
            return g;
        }
        loop {
            let x1 = 2.0 * self.mt.next_f64() - 1.0;
            let x2 = 2.0 * self.mt.next_f64() - 1.0;
            // numpy is compiled with floating-point contraction, so `x1*x1 + x2*x2` is one fused multiply-add
            let r2 = x1.mul_add(x1, x2 * x2);
            if r2 >= 1.0 || r2 == 0.0 {
                continue;
            }
            let f = (-2.0 * r2.ln() / r2).sqrt();
            self.gauss = Some(f * x1);
            return f * x2;
        }
    }

    /// `np.random.randn(rows, cols)` as a row-major vector.
    pub fn randn(&mut self, rows: usize, cols: usize) -> Vec<f64> {
        (0..rows * cols).map(|_| self.standard_normal()).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn python_random_reference_values() {
        // random.Random(42).random() == 0.6394267984578837 in CPython
        let mut r = PyRandom::new(42);
        assert_eq!(r.random(), 0.6394267984578837);
    }

    #[test]
    fn numpy_reference_values() {
        // np.random.seed(0); np.random.randn() == 1.764052345967664
        let mut s = NumpyRandomState::new(0);
        assert_eq!(s.standard_normal(), 1.764052345967664);
    }
}
