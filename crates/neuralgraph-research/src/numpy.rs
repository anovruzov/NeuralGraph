//! The numpy float arithmetic the research code relied on, reproduced bit for bit.
//!
//! The Python engine converted embeddings to `float32` and ranked them with `np.dot`, `np.linalg.norm` and
//! `M @ q`. Charges and rankings depend on the exact float32 results, and those come from the BLAS numpy links:
//! on macOS that is Apple's Accelerate (`numpy.show_config()` reports it for every numpy 2.x wheel), whose
//! `sdot`/`sgemv` kernels use a summation order no portable loop reproduces. So on macOS these helpers call the
//! same Accelerate routines numpy calls, and the results are identical to numpy's to the last bit. Elsewhere a
//! plain sequential loop stands in: Python's own results on Linux depend on which BLAS numpy was built with, so
//! there is no single reference to match there.
//!
//! Reductions numpy performs itself (`np.add.reduce`, and therefore `np.linalg.norm(..., axis=1)`) use numpy's
//! pairwise summation, implemented here exactly on every platform.

#[cfg(target_os = "macos")]
mod blas {
    #[link(name = "Accelerate", kind = "framework")]
    extern "C" {
        pub fn cblas_sdot(n: i32, x: *const f32, incx: i32, y: *const f32, incy: i32) -> f32;
        pub fn cblas_ddot(n: i32, x: *const f64, incx: i32, y: *const f64, incy: i32) -> f64;
        #[allow(clippy::too_many_arguments)]
        pub fn cblas_sgemv(
            order: i32,
            trans: i32,
            m: i32,
            n: i32,
            alpha: f32,
            a: *const f32,
            lda: i32,
            x: *const f32,
            incx: i32,
            beta: f32,
            y: *mut f32,
            incy: i32,
        );
        #[allow(clippy::too_many_arguments)]
        pub fn cblas_dgemv(
            order: i32,
            trans: i32,
            m: i32,
            n: i32,
            alpha: f64,
            a: *const f64,
            lda: i32,
            x: *const f64,
            incx: i32,
            beta: f64,
            y: *mut f64,
            incy: i32,
        );
    }
    pub const ROW_MAJOR: i32 = 101;
    pub const NO_TRANS: i32 = 111;
}

/// True when the float32 helpers are bit-identical to numpy on this platform (macOS, Accelerate).
pub const BITWISE_NUMPY: bool = cfg!(target_os = "macos");

/// `np.dot(a, b)` for two float32 vectors.
pub fn sdot(a: &[f32], b: &[f32]) -> f32 {
    assert_eq!(a.len(), b.len(), "sdot: length mismatch");
    if a.is_empty() {
        return 0.0;
    }
    #[cfg(target_os = "macos")]
    {
        // SAFETY: both slices are valid for `a.len()` contiguous elements; Accelerate only reads them.
        unsafe { blas::cblas_sdot(a.len() as i32, a.as_ptr(), 1, b.as_ptr(), 1) }
    }
    #[cfg(not(target_os = "macos"))]
    {
        a.iter().zip(b).fold(0.0f32, |s, (x, y)| s + x * y)
    }
}

/// `np.dot(a, b)` for two float64 vectors.
pub fn ddot(a: &[f64], b: &[f64]) -> f64 {
    assert_eq!(a.len(), b.len(), "ddot: length mismatch");
    if a.is_empty() {
        return 0.0;
    }
    #[cfg(target_os = "macos")]
    {
        // SAFETY: as in `sdot`.
        unsafe { blas::cblas_ddot(a.len() as i32, a.as_ptr(), 1, b.as_ptr(), 1) }
    }
    #[cfg(not(target_os = "macos"))]
    {
        a.iter().zip(b).fold(0.0f64, |s, (x, y)| s + x * y)
    }
}

/// `M @ x` for a row-major float32 matrix of `rows` x `x.len()`.
pub fn sgemv(m: &[f32], rows: usize, x: &[f32]) -> Vec<f32> {
    let cols = x.len();
    assert_eq!(m.len(), rows * cols, "sgemv: shape mismatch");
    let mut y = vec![0.0f32; rows];
    if rows == 0 || cols == 0 {
        return y;
    }
    #[cfg(target_os = "macos")]
    {
        // SAFETY: `m` holds rows*cols elements, `x` cols, `y` rows; Accelerate writes only `y`.
        unsafe {
            blas::cblas_sgemv(
                blas::ROW_MAJOR,
                blas::NO_TRANS,
                rows as i32,
                cols as i32,
                1.0,
                m.as_ptr(),
                cols as i32,
                x.as_ptr(),
                1,
                0.0,
                y.as_mut_ptr(),
                1,
            )
        };
    }
    #[cfg(not(target_os = "macos"))]
    {
        for (r, out) in y.iter_mut().enumerate() {
            *out = sdot(&m[r * cols..(r + 1) * cols], x);
        }
    }
    y
}

/// `M @ x` for a row-major float64 matrix of `rows` x `x.len()`.
pub fn dgemv(m: &[f64], rows: usize, x: &[f64]) -> Vec<f64> {
    let cols = x.len();
    assert_eq!(m.len(), rows * cols, "dgemv: shape mismatch");
    let mut y = vec![0.0f64; rows];
    if rows == 0 || cols == 0 {
        return y;
    }
    #[cfg(target_os = "macos")]
    {
        // SAFETY: as in `sgemv`.
        unsafe {
            blas::cblas_dgemv(
                blas::ROW_MAJOR,
                blas::NO_TRANS,
                rows as i32,
                cols as i32,
                1.0,
                m.as_ptr(),
                cols as i32,
                x.as_ptr(),
                1,
                0.0,
                y.as_mut_ptr(),
                1,
            )
        };
    }
    #[cfg(not(target_os = "macos"))]
    {
        for (r, out) in y.iter_mut().enumerate() {
            *out = ddot(&m[r * cols..(r + 1) * cols], x);
        }
    }
    y
}

/// `np.linalg.norm(x)` for a float32 vector: numpy computes `sqrt(x.dot(x))` in float32.
pub fn norm_f32(x: &[f32]) -> f32 {
    sdot(x, x).sqrt()
}

/// `np.linalg.norm(x)` for a float64 vector.
pub fn norm_f64(x: &[f64]) -> f64 {
    ddot(x, x).sqrt()
}

/// numpy's pairwise summation (`np.add.reduce` over a contiguous axis), float32 accumulation.
pub fn pairwise_sum_f32(x: &[f32]) -> f32 {
    let n = x.len();
    if n < 8 {
        // numpy starts from -0.0 so that a sum of negative zeros stays -0.0
        let mut res = -0.0f32;
        for v in x {
            res += *v;
        }
        res
    } else if n <= 128 {
        let mut r = [0.0f32; 8];
        r.copy_from_slice(&x[..8]);
        let mut i = 8;
        while i < n - (n % 8) {
            for j in 0..8 {
                r[j] += x[i + j];
            }
            i += 8;
        }
        let mut res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
        while i < n {
            res += x[i];
            i += 1;
        }
        res
    } else {
        let mut n2 = n / 2;
        n2 -= n2 % 8;
        pairwise_sum_f32(&x[..n2]) + pairwise_sum_f32(&x[n2..])
    }
}

/// numpy's pairwise summation, float64.
pub fn pairwise_sum_f64(x: &[f64]) -> f64 {
    let n = x.len();
    if n < 8 {
        let mut res = -0.0f64;
        for v in x {
            res += *v;
        }
        res
    } else if n <= 128 {
        let mut r = [0.0f64; 8];
        r.copy_from_slice(&x[..8]);
        let mut i = 8;
        while i < n - (n % 8) {
            for j in 0..8 {
                r[j] += x[i + j];
            }
            i += 8;
        }
        let mut res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
        while i < n {
            res += x[i];
            i += 1;
        }
        res
    } else {
        let mut n2 = n / 2;
        n2 -= n2 % 8;
        pairwise_sum_f64(&x[..n2]) + pairwise_sum_f64(&x[n2..])
    }
}

/// `np.linalg.norm(M, axis=1)` for a row-major float32 matrix: `sqrt(add.reduce(M*M, axis=1))`.
pub fn row_norms_f32(m: &[f32], cols: usize) -> Vec<f32> {
    if cols == 0 {
        return Vec::new();
    }
    m.chunks(cols)
        .map(|row| {
            let squares: Vec<f32> = row.iter().map(|v| v * v).collect();
            pairwise_sum_f32(&squares).sqrt()
        })
        .collect()
}

/// `np.linalg.norm(M, axis=1)` for a row-major float64 matrix.
pub fn row_norms_f64(m: &[f64], cols: usize) -> Vec<f64> {
    if cols == 0 {
        return Vec::new();
    }
    m.chunks(cols)
        .map(|row| {
            let squares: Vec<f64> = row.iter().map(|v| v * v).collect();
            pairwise_sum_f64(&squares).sqrt()
        })
        .collect()
}

/// `np.array(values, dtype=np.float32)`: round to nearest, ties to even (what `as f32` does).
pub fn to_f32(values: &[f64]) -> Vec<f32> {
    values.iter().map(|v| *v as f32).collect()
}

/// `np.argsort(-values)` for distinct values. numpy's default sort is not stable, so the order of exactly equal
/// values was unspecified in the Python run; ties keep index order here.
pub fn argsort_desc_f32(values: &[f32]) -> Vec<usize> {
    let mut idx: Vec<usize> = (0..values.len()).collect();
    idx.sort_by(|a, b| (-values[*a]).partial_cmp(&-values[*b]).unwrap_or(std::cmp::Ordering::Equal));
    idx
}

/// Normalise a float32 vector the way the research code wrote `v / (np.linalg.norm(v) + eps)`.
pub fn normalized_f32_eps(v: &[f32], eps: f32) -> Vec<f32> {
    let d = norm_f32(v) + eps;
    v.iter().map(|x| x / d).collect()
}

/// A row-major float32 matrix whose rows are `v / (norm(v) + eps)` (the `M /= np.linalg.norm(M, axis=1,
/// keepdims=True) + eps` idiom). Returns the flat matrix and its column count.
pub fn normalized_rows_f32(rows: &[&[f64]], eps: f32) -> (Vec<f32>, usize) {
    let cols = rows.first().map(|r| r.len()).unwrap_or(0);
    let mut m: Vec<f32> = Vec::with_capacity(rows.len() * cols);
    for r in rows {
        assert_eq!(r.len(), cols, "ragged embedding matrix");
        m.extend(r.iter().map(|v| *v as f32));
    }
    let norms = row_norms_f32(&m, cols);
    for (row, n) in m.chunks_mut(cols.max(1)).zip(norms) {
        let d = n + eps;
        for v in row {
            *v /= d;
        }
    }
    (m, cols)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pairwise_matches_sequential_for_short_inputs() {
        let x = [1.0f32, 2.0, 3.0];
        assert_eq!(pairwise_sum_f32(&x), 6.0);
        assert!(pairwise_sum_f32(&[]).is_sign_negative());
    }

    #[test]
    fn dot_and_norm_basics() {
        assert_eq!(sdot(&[1.0, 2.0], &[3.0, 4.0]), 11.0);
        assert_eq!(norm_f32(&[3.0, 4.0]), 5.0);
        assert_eq!(sgemv(&[1.0, 0.0, 0.0, 1.0], 2, &[5.0, 7.0]), vec![5.0, 7.0]);
    }
}
