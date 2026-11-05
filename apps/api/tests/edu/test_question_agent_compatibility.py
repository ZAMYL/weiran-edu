"""Completely synthetic, local-only mapping through real SQLite and original Kernel."""

from dataclasses import replace
from fractions import Fraction

import pytest
from app.core.errors import APIError
from app.edu.question_provider import QuestionAssessmentProvider

from prototypes.adaptive_assessment.analysis import Kernel
from prototypes.adaptive_assessment.data import PrototypeError
from prototypes.question_provider_probe.adapter import admit

from .test_question_provider import official as official
from .test_question_xlsx import synthetic


def test_thirteen_actual_scales_exact_statistics_and_replayed_refs(official, runtime):
    provider, ref = official
    lease = provider.acquire(runtime.p, ref["batch_id"], ref["snapshot_id"])
    envelope = admit(provider, lease)
    kernel = Kernel(envelope)
    assert len(kernel.items) == 13
    assert kernel.items["Q13"].scale.maximum == "130"
    assert all(
        i.kind == "question" and i.confirmed_profile_code is None for i in kernel.items.values()
    )
    for item in kernel.items:
        result, source = kernel.statistics(item)
        assert (
            result["mean"],
            result["denominator"],
            result["valid_count"],
            result["excluded_count"],
        ) == (Fraction(11, 4), 20, 20, 0)
        assert len(source.references) == 20
        w = provider.replay_witness(lease, source.references[0])
        assert w.item_id == item and w.source_sha256 == lease.source_sha256
    with pytest.raises(PrototypeError, match="relation_scale_or_level_unknown"):
        kernel.relation("Q01", "Q02")
    with pytest.raises(APIError) as error:
        provider.acquire(runtime.p, ref["batch_id"], ref["snapshot_id"], purpose="external_model")
    assert error.value.code == "FORMAL_MODEL_CALL_DISABLED"
    runtime.admin.revoke_tokens(runtime.teacher)
    with pytest.raises(APIError):
        envelope.validate()


def test_missing_remains_excluded_and_invalid_is_explicitly_blocked(client, runtime):
    def import_book(change):
        response = client.post(
            "/api/v1/edu/imports/preview",
            files={"file": ("synthetic.xlsx", synthetic(change), "application/octet-stream")},
        )
        assert response.status_code == 201
        preview = response.json()
        response = client.post(
            f"/api/v1/edu/imports/{preview['import_id']}/commit",
            json={"preview_version": preview["preview_version"], "acknowledge_warnings": True},
        )
        assert response.status_code == 201
        return response.json()

    ref = import_book(lambda w: setattr(w["Sheet2"]["B2"], "value", None))
    provider = QuestionAssessmentProvider(runtime.service)
    lease = provider.acquire(runtime.p, ref["batch_id"], ref["snapshot_id"])
    envelope = admit(provider, lease)
    result, source = Kernel(envelope).statistics("Q01")
    assert (
        result["mean"],
        result["valid_count"],
        result["excluded_count"],
        result["denominator"],
    ) == (Fraction(11, 4), 19, 1, 20)
    assert len(source.references) == 19
    assert result["states"] == {"missing": 1, "valid": 19}
    forged = replace(envelope, source_bytes=b"client-json")
    with pytest.raises(PrototypeError, match="question_source_binding_mismatch"):
        forged.validate()
    invalid = import_book(lambda w: setattr(w["Sheet2"]["B2"], "value", "not-a-score"))
    lease = provider.acquire(runtime.p, invalid["batch_id"], invalid["snapshot_id"])
    with pytest.raises(PrototypeError, match="question_invalid_state_mapping_not_implemented"):
        admit(provider, lease)
