"""Choose and evaluate the theoretical PSF model for one channel (THE-01, THE-08)."""

from __future__ import annotations

from dataclasses import dataclass

from psfwidget.io.model import AcquisitionMetadata, ChannelMetadata, Modality
from psfwidget.theory import analytic
from psfwidget.theory.analytic import AnalyticWidth
from psfwidget.theory.scalar import ism_width, scalar_width


@dataclass(frozen=True)
class Theory:
    """Theoretical FWHM for one channel.

    ``lateral_um`` and ``axial_um`` come from the numerical scalar model and
    are the reference for measured/theoretical ratios. ``analytic`` holds the
    closed-form values for the same settings where a formula exists.
    """

    lateral_um: float
    axial_um: float
    model: str
    modality: Modality
    excitation_nm: float | None
    emission_nm: float | None
    """``None`` for multiphoton when the emission is unknown; it does not enter the model."""
    numerical_aperture: float
    refractive_index: float
    pinhole_au: float | None
    analytic: AnalyticWidth | None
    notes: tuple[str, ...] = ()
    ism_lateral_um: float | None = None
    """FWHM of the image-scanning (pixel-reassignment) model, for resolution-enhanced
    modes such as NSPARC and Live-SR (THE-09)."""
    ism_axial_um: float | None = None


class TheoryUnavailableError(ValueError):
    """Raised when a channel's metadata is not enough to compute a theoretical PSF."""


def pinhole_airy_units(
    channel: ChannelMetadata, metadata: AcquisitionMetadata
) -> tuple[float, str]:
    """Return the pinhole size in Airy units at the emission wavelength, and any assumption made.

    The second value is empty when the size in Airy units was recorded, and
    explains the conversion when it was derived from a physical size.

    Raises
    ------
    TheoryUnavailableError
        If neither a size in Airy units nor a physical size and magnification
        is known.
    """
    if channel.pinhole_airy is not None:
        return channel.pinhole_airy.value, ""
    if (
        channel.pinhole_um is not None
        and metadata.magnification is not None
        and channel.emission_nm is not None
        and metadata.numerical_aperture is not None
    ):
        in_sample = channel.pinhole_um.value / metadata.magnification.value
        airy = analytic.airy_unit_um(channel.emission_nm.value, metadata.numerical_aperture.value)
        return in_sample / airy, (
            f"pinhole {channel.pinhole_um.value:g} µm / objective magnification "
            f"{metadata.magnification.value:g} = {in_sample / airy:.2f} AU; assumes no "
            "magnification between the objective and the pinhole"
        )
    raise TheoryUnavailableError(
        "the pinhole size is unknown; set pinhole_airy for this channel in the instrument profile"
    )


def _enhancement_notes(metadata: AcquisitionMetadata, modality: Modality) -> list[str]:
    enhancement = metadata.resolution_enhancement
    if enhancement is None:
        return []
    base = modality.value.replace("_", "-")
    return [
        f"{enhancement.value} is not modelled; compared with the {base} model, "
        "so ratios below 1 are expected (THE-08)"
    ]


def channel_theory(channel: ChannelMetadata, metadata: AcquisitionMetadata) -> Theory:
    """Return the theoretical FWHM of one channel.

    Raises
    ------
    TheoryUnavailableError
        If a value the model needs is missing.
    """
    multiphoton = metadata.modality is not None and (
        metadata.modality.value is Modality.MULTIPHOTON
    )
    wavelength = (
        ("excitation_nm", channel.excitation_nm)
        if multiphoton
        else ("emission_nm", channel.emission_nm)
    )
    missing = [
        name
        for name, item in (
            ("numerical_aperture", metadata.numerical_aperture),
            ("immersion_ri", metadata.immersion_ri),
            ("modality", metadata.modality),
            wavelength,
        )
        if item is None
    ]
    if missing:
        raise TheoryUnavailableError("missing " + ", ".join(missing))
    assert metadata.numerical_aperture is not None
    assert metadata.immersion_ri is not None
    assert metadata.modality is not None
    na = metadata.numerical_aperture.value
    n = metadata.immersion_ri.value
    modality = metadata.modality.value
    notes: list[str] = []

    if multiphoton:
        assert channel.excitation_nm is not None
        laser = channel.excitation_nm.value
        scalar = scalar_width("multiphoton", laser, 0.0, na, n)
        multiphoton_form = analytic.multiphoton(laser, na, n)
        if laser < 650:
            notes.append(
                f"excitation {laser:g} nm is short for two-photon excitation; check that "
                "excitation_nm is the laser wavelength, not half of it"
            )
        return Theory(
            lateral_um=scalar.lateral_um,
            axial_um=scalar.axial_um,
            model=scalar.model,
            modality=modality,
            excitation_nm=laser,
            emission_nm=None if channel.emission_nm is None else channel.emission_nm.value,
            numerical_aperture=na,
            refractive_index=n,
            pinhole_au=None,
            analytic=multiphoton_form,
            notes=(*notes, *_enhancement_notes(metadata, modality)),
        )

    assert channel.emission_nm is not None
    emission = channel.emission_nm.value
    if modality is Modality.WIDEFIELD:
        excitation = None if channel.excitation_nm is None else channel.excitation_nm.value
        scalar = scalar_width("widefield", None, emission, na, n)
        closed_form: AnalyticWidth | None = analytic.widefield(emission, na, n)
        pinhole = None
    else:
        if channel.excitation_nm is None:
            raise TheoryUnavailableError("missing excitation_nm")
        excitation = channel.excitation_nm.value
        pinhole, how = pinhole_airy_units(channel, metadata)
        if how:
            notes.append(how)
        scalar = scalar_width(modality.value, excitation, emission, na, n, round(pinhole, 4))
        closed_form = analytic.confocal(excitation, emission, na, n, pinhole)
        if closed_form is None:
            notes.append("no closed-form formula between 0.25 and 1 AU")

    notes.extend(_enhancement_notes(metadata, modality))
    ism = None
    if metadata.resolution_enhancement is not None and pinhole is not None:
        assert excitation is not None
        ism = ism_width(excitation, emission, na, n, round(pinhole, 4))
        notes.append(
            f"also compared with the image-scanning model ({ism.model}): lateral "
            f"{ism.lateral_um * 1000:.0f} nm, axial {ism.axial_um * 1000:.0f} nm (THE-09)"
        )
    return Theory(
        lateral_um=scalar.lateral_um,
        axial_um=scalar.axial_um,
        model=scalar.model,
        modality=modality,
        excitation_nm=excitation,
        emission_nm=emission,
        numerical_aperture=na,
        refractive_index=n,
        pinhole_au=pinhole,
        analytic=closed_form,
        notes=tuple(notes),
        ism_lateral_um=None if ism is None else ism.lateral_um,
        ism_axial_um=None if ism is None else ism.axial_um,
    )


def nyquist_voxel_um(theory: Theory, samples_per_fwhm: float = 2.3) -> tuple[float, float]:
    """Return the largest ``(lateral, axial)`` voxel size that samples the theoretical PSF.

    The criterion is ``samples_per_fwhm`` voxels across the theoretical FWHM
    (MET-16).
    """
    return theory.lateral_um / samples_per_fwhm, theory.axial_um / samples_per_fwhm
