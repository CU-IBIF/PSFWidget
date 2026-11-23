# How PSFWidget Calculates the Theoretical PSF

**Applies to:** version 0.0.1.dev0, 2026-11-23
**Requirements:** THE-01 to THE-10 ([Project Plan](PLAN.md) §3.4)
**Code:** `src/psfwidget/theory/` (`model.py` chooses the model; `scalar.py`,
`optics.py`, `analytic.py`, and `psf3d.py` compute it)

Every PSFWidget report compares the measured beads with the PSF the
instrument should give. This page explains how that expected PSF is
calculated, which values it needs, and what it does not model. For how
to read the results, see the [user manual](manual/USER_MANUAL.md) §6.

In short: PSFWidget integrates the focal field of an ideal objective
numerically, combines the excitation and emission PSFs according to the
microscope's modality, and measures the FWHM of the result. The
textbook formulas are reported alongside for reference. The numerical model
is the reference for every *measured ÷ theory* ratio.

---

## 1. Inputs

For each channel, the model needs:

| Value | Used by | Where it comes from |
|---|---|---|
| Numerical aperture (NA) | all modalities | file, profile, or GUI metadata table |
| Immersion refractive index (n) | all modalities | file, profile, or GUI |
| Modality | all | file or profile: widefield, confocal, spinning disk, multiphoton |
| Emission wavelength | widefield, confocal, spinning disk | profile (the bead dye's emission peak); files often record a filter range instead |
| Excitation wavelength | confocal, spinning disk; for multiphoton, the **laser** wavelength | file or profile |
| Pinhole size in Airy units | confocal, spinning disk | file or profile (`pinhole_airy`) |

If the pinhole is recorded only in µm, it is converted to Airy units:
pinhole ÷ objective magnification ÷ Airy-unit diameter (1.22 λ_em / NA). This
assumes no extra magnification between the objective and the pinhole, and the
report says so in a note. If any required value is missing, the channel has
no theory, and the report states which value is missing.

---

## 2. The core model: scalar, high-NA

By default, PSFWidget computes the amplitude PSF of an aberration-free
objective with a uniform pupil:

    E(r, z) = ∫₀¹ J₀(k · NA · r · ρ) · exp(i · k · z · √(n² − NA² ρ²)) · ρ dρ

with k = 2π / λ (vacuum wavelength). The intensity PSF is |E|². The integral
is evaluated numerically (400 nodes across the pupil).

- **Kept:** the exact high-NA defocus phase, √(n² − NA² ρ²), so the axial
  width is right at high NA, where the paraxial approximation fails.
- **Ignored:** polarisation and apodisation (see §3 for the vectorial model).
- **Assumed:** the sample's refractive index matches the immersion medium
  (see §3 for mismatch).

### 2.1 Combining excitation and emission by modality

| Modality | System PSF |
|---|---|
| **Widefield** | the emission PSF |
| **Confocal** | the excitation PSF × the emission PSF summed over the pinhole disc projected into the sample |
| **Spinning disk** | as confocal, treating the disk as a single pinhole (crosstalk between pinholes is ignored) |
| **Multiphoton** (two-photon) | the excitation PSF squared, at the laser wavelength. Detection is non-descanned, so the emission does not enter |

For confocal, the pinhole integral moves smoothly between the two limits.
- **Closed pinhole:** the PSF is excitation × emission, the sharpest case.
- **Open pinhole:** it approaches the excitation PSF alone.

This is why any pinhole size, including 0.25–1 AU where no closed-form formula
exists, can be handled.

### 2.2 Measuring the theoretical FWHM

The lateral and axial intensity profiles through the PSF maximum are sampled
finely (40 samples per resolution element laterally, 1,200 axially). The FWHM
is where each profile crosses half its maximum, found by linear interpolation
between samples. These two numbers are the **theoretical lateral and axial
FWHM** in the reports.

---

## 3. Optional refinements

An instrument profile's `[theory]` table can ask for either or both of two
refinements (user manual §4):

```toml
[theory]
model = "vectorial"    # Richards–Wolf; default "scalar"
sample_ri = 1.46       # mountant index, for refractive-index mismatch
bead_depth_um = 0      # bead depth below the coverslip
coverslip_ri = 1.523   # only if different from the objective's design
coverslip_um = 170
```

### 3.1 Vectorial (Richards–Wolf) focusing

At high NA, light arriving at steep angles is strongly polarised along the
optical axis, which the scalar model ignores. The vectorial model builds the
focal field of an aplanatic objective (apodisation √cos θ) from three
integrals:

    I₀ = ∫ √cosθ (1 + cosθ) J₀(k r s) e^{iΦ} ρ dρ / cosθ
    I₁ = ∫ √cosθ sinθ        J₁(k r s) e^{iΦ} ρ dρ / cosθ
    I₂ = ∫ √cosθ (1 − cosθ) J₂(k r s) e^{iΦ} ρ dρ / cosθ

with s = NA·ρ = n·sinθ. For unpolarised light the intensity is
|I₀|² + 2|I₁|² + |I₂|². At NA 1.4 it is about 10% wider laterally than the
scalar model; at low NA the two agree.

### 3.2 Refractive-index mismatch and coverslip (Gibson–Lanni)

Beads below the coverslip in a mountant whose index differs from the
immersion oil are imaged through extra optical path. The pupil phase gains
that path difference:

    Φ = k [ z·√(n_i² − s²) + d·√(n_s² − s²) + t_g·√(n_g² − s²) − t_g*·√(n_g*² − s²) ]

where:
- z is the focus (stage) position;
- d is the bead depth;
- n_i, n_s, and n_g are the immersion, sample, and coverslip indices;
- t_g is the coverslip thickness, and the starred values are the objective's
  design values.

Mismatch makes the PSF longer in z and asymmetric, and moves its peak away
from the nominal focus. So the widths are measured around the peak,
wherever it falls, and the focus shift is recorded.

When the NA is larger than the sample's index, the aperture is limited to
the sample index, as in the Gibson–Lanni model. Rays beyond that angle do
not propagate into the sample.

---

## 4. Resolution-enhanced modes (NSPARC, Live-SR)

Image-scanning and pixel-reassignment modes give PSFs sharper than their
underlying modality. PSFWidget compares them two ways:

1. **With the model of their base modality** (confocal or spinning disk).
   Ratios below 1 are expected, and the report says so (THE-08).
2. **With an image-scanning model** (THE-09):
   - each detector element at offset d contributes the excitation PSF shifted
     by a·d times the emission PSF shifted by −(1 − a)·d, with the
     reassignment factor a = λ_ex² / (λ_ex² + λ_em²) (Sheppard 1988; Müller
     and Enderlein 2010);
   - the contributions of a disc-shaped detector of the given size in Airy
     units are summed;
   - as the detector shrinks, the result tends to the closed-pinhole confocal
     PSF;
   - optical reassignment (Live-SR) is modelled the same way.

---

## 5. Closed-form formulas (for reference)

These standard formulas are reported next to the numerical values. They are
not used for the ratios.

| Case | Lateral FWHM | Axial FWHM |
|---|---|---|
| Widefield | 0.51 λ_em / NA | 0.88 λ_em / (n − √(n² − NA²)) |
| Confocal, pinhole ≤ 0.25 AU | 0.37 λ̄ / NA | 0.64 λ̄ / (n − √(n² − NA²)) |
| Confocal, pinhole ≥ 1 AU | 0.51 λ_ex / NA | 0.88 λ_ex / (n − √(n² − NA²)) |
| Multiphoton | 2√(ln 2) · ω_xy | 2√(ln 2) · ω_z |

- **λ̄ (closed confocal pinhole):** the mean wavelength,
  √2 · λ_ex · λ_em / √(λ_ex² + λ_em²).
- **Confocal and widefield source:** Wilhelm et al., *Confocal Laser
  Scanning Microscopy: Principles* (Carl Zeiss).
- **Multiphoton source:** Zipfel, Williams and Webb 2003, with ω_xy =
  0.320 λ_ex / (√2 NA) for NA ≤ 0.7, ω_xy = 0.325 λ_ex / (√2 NA^0.91) above it,
  and ω_z = 0.532 λ_ex / √2 · 1 / (n − √(n² − NA²)).

Between 0.25 and 1 AU there is no closed form. The report says so, and only the
numerical value is given.

The Zeiss publication's open-pinhole axial formula includes a pinhole term,
but that is the section thickness for a fluorescent *layer*. A bead is a point
source, so that formula is not used.

---

## 6. Bead size

A 100 nm bead is not a point, so the measured image is the PSF blurred by
the bead. PSFWidget removes the bead's width from each measured FWHM before
comparing it with the theory:

- the bead (a uniform sphere of diameter D) is replaced by the Gaussian with
  the same variance, D²/20, whose FWHM is 0.527 D;
- that width is subtracted in quadrature: corrected = √(measured² − (0.527 D)²).

The correction is applied when the bead diameter is known (profile
`bead_diameter_nm`) and `bead_size_correction` is on (the default). If the
diameter is unknown, the report warns that the values are uncorrected. For a
100 nm bead the correction is small: a 250 nm measured FWHM becomes 244 nm.

---

## 7. The 3D model image

Some results compare whole images rather than widths:
- the Strehl ratio and the similarity score of the averaged PSF;
- the *Model* row of the report's image panel;
- the dashed model curve in the profile figure.

For these, the model is built as a 3D image at the stack's voxel size:

1. The model of §2–3 is evaluated on a radial grid and mapped onto a 3D grid
   finer than the voxels.
2. For confocal and spinning disk, the emission PSF is convolved with the
   pinhole disc plane by plane before multiplying by the excitation PSF.
3. It is convolved with a solid sphere the size of the bead.
4. It is averaged into the real voxels (each voxel integrates the light that
   falls in it).
5. It is scaled to a total of 1.

The **Strehl ratio estimate** is the peak of the averaged bead image divided
by the peak of this model image, at equal total intensity. Below 1 means
aberrated. The **similarity** is the Pearson correlation between the two
images.

The theory also sets the **Nyquist check**: a voxel larger than the
theoretical FWHM ÷ 2.3 (setting `nyquist_samples_per_fwhm`) triggers an
"undersampled" warning.

---

## 8. What is not modelled

- Polarisation and apodisation in the default (scalar) model; use
  `model = "vectorial"` at high NA.
- Supercritical-angle fluorescence from beads touching the coverslip.
- Fresnel transmission losses at the interfaces.
- Crosstalk between spinning-disk pinholes.
- Aberrations other than refractive-index and coverslip mismatch (for
  example, a misaligned correction collar or a tilted coverslip). Those are
  what the comparison is meant to reveal.
- The specific processing of NSPARC and Live-SR beyond pixel reassignment.

---

## 9. How the models are verified

Automated tests (in `tests/test_theory*.py` and `tests/test_averaged_psf.py`)
check, among others, that:

- the closed-form formulas match hand calculations;
- the scalar widefield lateral profile is the Airy pattern;
- the confocal model with a point detector matches the closed-pinhole
  formula, and widens towards the open-pinhole limit as the pinhole opens;
- the multiphoton model agrees with Zipfel's formulas to within about 6% for
  NA 0.8 to 1.4;
- the vectorial model reduces to the scalar model at low NA;
- the mismatch model agrees with the exact Gibson–Lanni integration of
  MicroscPSF-Py (an independent implementation) to within 0.3%, for beads 0,
  2, 5, and 10 µm deep in water under an NA 1.4 oil objective;
- the image-scanning model tends to the closed-pinhole confocal PSF as the
  detector shrinks;
- beads rendered from the 3D model have a Strehl ratio of 1.

VAL-04 (validation on well-aligned objectives at the facility) is still to
do; see [STATUS](STATUS.md).

---

## 10. References

- Gibson SF, Lanni F. Experimental test of an analytical model of aberration
  in an oil-immersion objective lens used in three-dimensional light
  microscopy. *J. Opt. Soc. Am. A* 1991, 8:1601–1613.
- Müller CB, Enderlein J. Image scanning microscopy. *Phys. Rev. Lett.* 2010,
  104:198101.
- Novotny L, Hecht B. *Principles of Nano-Optics*, chapter 3. Cambridge
  University Press.
- Richards B, Wolf E. Electromagnetic diffraction in optical systems II.
  *Proc. R. Soc. Lond. A* 1959, 253:358–379.
- Sheppard CJR. Super-resolution in confocal imaging. *Optik* 1988,
  80:53–54.
- Wilhelm S, Gröbler B, Gluch M, Heinz H. *Confocal Laser Scanning
  Microscopy: Principles*. Carl Zeiss, Jena.
- Zipfel WR, Williams RM, Webb WW. Nonlinear magic: multiphoton microscopy
  in the biosciences. *Nat. Biotechnol.* 2003, 21:1369–1377.
