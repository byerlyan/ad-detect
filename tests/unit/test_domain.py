import pytest

from ad_detect.domain import Classification, DetectorEvidence
from ad_detect.domain.models import probabilities


def test_probabilities_require_all_classes_and_sum_to_one() -> None:
    with pytest.raises(ValueError, match="sum to 1"):
        probabilities(0.2, 0.2, 0.2)


def test_invalid_evidence_cannot_claim_commercial_probability() -> None:
    with pytest.raises(ValueError, match="invalid evidence"):
        DetectorEvidence(
            detector_id="bad",
            detector_version="1",
            window_start_ns=0,
            window_end_ns=1,
            probabilities=probabilities(0.0, 1.0, 0.0),
            valid=False,
        )


def test_probability_mapping_uses_domain_enum_keys() -> None:
    result = probabilities(0.7, 0.2, 0.1)
    assert result[Classification.PROGRAM] == 0.7
