"""Round 16: product-image intake, the listing-quality pack,
and the governed vision call.

The planted truth (generator constants): five images for six
SKUs — the mirror has none, the candle duo's is 240x180 against
the pack's 800x800 minimums, the vase's shows the candle duo,
the towel set's is PNG bytes named as a JPEG. The dinner set
and the throw are healthy.
"""

from __future__ import annotations

import pytest
from conftest import make_settings

from smb_commerce import generator
from smb_commerce.access import Caller
from smb_commerce.images import (
    IMAGE_KIND,
    measure_image,
    object_key_for,
    write_png,
)
from smb_commerce.listing import LISTING_PACK
from smb_commerce.modelport import (
    GovernedModelPort,
    MetaMuseModelPort,
    ProfileNotConfigured,
)
from smb_commerce.service import CommerceService

EXPECTED_FINDINGS = {
    ("missing_image", generator.IMAGELESS_SKU),
    ("below_min_dimensions", generator.SMALL_IMAGE_SKU),
    ("format_mismatch", generator.FORMAT_MISMATCH_SKU),
    ("vision_mismatch", generator.VISION_MISMATCH_SKU),
}


def _fresh_service(tmp_path) -> CommerceService:
    return CommerceService(make_settings(tmp_path))


def _healthy_png(subject: str = "stoneware_dinner_set") -> bytes:
    return write_png(1200, 900, (196, 120, 86), subject_class=subject)


# -- the intake port -------------------------------------------------------


@pytest.mark.parametrize("source_system,as_of", [
    (None, "2026-01-04"), ("", "2026-01-04"), ("  ", "2026-01-04"),
    ("hartwell-studio", None), ("hartwell-studio", ""),
    ("hartwell-studio", "  "),
])
def test_intake_refuses_missing_provenance(tmp_path, source_system, as_of):
    service = _fresh_service(tmp_path)
    outcome = service.ingest_product_image(
        sku="HW-DIN-001", filename="hero.png", data=_healthy_png(),
        declared_class="stoneware_dinner_set",
        source_system=source_system, as_of=as_of)
    assert outcome.status == "refused"
    assert outcome.reason == "missing_provenance"
    assert service.store.count_facts(IMAGE_KIND) == 0


def test_intake_refuses_unreadable_bytes(tmp_path):
    service = _fresh_service(tmp_path)
    outcome = service.ingest_product_image(
        sku="HW-DIN-001", filename="hero.png",
        data=b"this is not an image at all",
        declared_class="stoneware_dinner_set",
        source_system=generator.SOURCE_STUDIO, as_of="2026-01-04")
    assert outcome.status == "refused"
    assert outcome.reason == "unreadable_image"
    assert service.store.count_facts(IMAGE_KIND) == 0


def test_intake_measures_stores_and_dedupes(tmp_path):
    service = _fresh_service(tmp_path)
    data = _healthy_png()
    outcome = service.ingest_product_image(
        sku="HW-DIN-001", filename="hero.png", data=data,
        declared_class="stoneware_dinner_set",
        source_system=generator.SOURCE_STUDIO, as_of="2026-01-04")
    assert outcome.status == "accepted"
    payload = outcome.fact.payload
    assert payload["format"] == "png"
    assert (payload["width"], payload["height"]) == (1200, 900)
    assert payload["declared_format"] == "png"
    assert payload["byte_count"] == len(data)
    assert service.objects.get(payload["object_key"]) == data
    assert payload["object_key"] == object_key_for(payload["content_hash"])
    again = service.ingest_product_image(
        sku="HW-DIN-001", filename="hero.png", data=data,
        declared_class="stoneware_dinner_set",
        source_system=generator.SOURCE_STUDIO, as_of="2026-01-04")
    assert again.status == "duplicate"
    assert service.store.count_facts(IMAGE_KIND) == 1


def test_measure_reads_the_fixture_formats():
    png = measure_image(_healthy_png())
    assert (png.format, png.width, png.height) == ("png", 1200, 900)
    assert png.subject_class == "stoneware_dinner_set"
    gif = measure_image(b"GIF89a" + bytes([10, 0, 20, 0]) + b"\x00" * 8)
    assert (gif.format, gif.width, gif.height) == ("gif", 10, 20)


# -- the generator fixtures --------------------------------------------------


def test_generator_plants_the_stated_images(service):
    report = service.generate_product_images()
    assert (report.accepted, report.duplicates, report.refused) == (5, 0, 0)
    facts = {f.payload["sku"]: f
             for f in service.store.list_facts(IMAGE_KIND)}
    assert set(facts) == {spec.sku for spec in generator.SKUS} - {
        generator.IMAGELESS_SKU}
    towel = facts[generator.FORMAT_MISMATCH_SKU].payload
    assert towel["declared_format"] == "jpeg"
    assert towel["format"] == "png"
    candle = facts[generator.SMALL_IMAGE_SKU].payload
    assert (candle["width"], candle["height"]) == generator.SMALL_IMAGE_SIZE
    for fact in facts.values():
        assert fact.source_system == generator.SOURCE_STUDIO
        assert fact.as_of == generator.END.isoformat()
    second = service.generate_product_images()
    assert (second.accepted, second.duplicates) == (0, 5)


# -- the diagnosis -----------------------------------------------------------


def test_diagnosis_names_exactly_the_planted_truth(service):
    service.generate_product_images()
    artefact = service.diagnose_listing_quality()
    assert artefact.payload["pack_version"] == LISTING_PACK["version"]
    named = {(f["class"], f["sku"]) for f in artefact.payload["findings"]}
    assert named == EXPECTED_FINDINGS
    assert artefact.payload["unjudged"] == []
    assert service.listing.verify(artefact) == []


def test_vision_finding_is_labelled_with_its_judgement(service):
    service.generate_product_images()
    artefact = service.diagnose_listing_quality()
    finding = next(f for f in artefact.payload["findings"]
                   if f["class"] == "vision_mismatch")
    judgement = service.store.get_artefact(finding["judgement_artefact_id"])
    assert judgement is not None
    assert judgement.kind == "image_vision_judgement"
    assert finding["judgement_profile"] == judgement.payload["profile"]
    assert finding["judgement_model"] == judgement.payload["model"]
    assert finding["pack_version"] == LISTING_PACK["version"]
    assert finding["judged_class"] == judgement.payload["subject_class"]
    assert finding["declared_class"] == "ceramic_vase"
    for field in ("profile", "model", "tier", "pack_version", "call_id"):
        assert judgement.payload[field]
    calls = service.store.list_model_calls(
        f"vision-{judgement.payload['image_content_hash'][:20]}")
    assert [c.call_id for c in calls] == [judgement.payload["call_id"]]
    assert service.model.reconcile(
        f"vision-{judgement.payload['image_content_hash'][:20]}") == []
    contexts = [a for a in service.store.list_artefacts("context_assembly")
                if a.payload["call_id"] == judgement.payload["call_id"]]
    assert len(contexts) == 1


def test_diagnosis_is_idempotent_and_reuses_judgements(service):
    service.generate_product_images()
    first = service.diagnose_listing_quality()
    calls_before = len(service.store.list_model_calls())
    second = service.diagnose_listing_quality()
    assert second.artefact_id == first.artefact_id
    assert len(service.store.list_artefacts("image_vision_judgement")) == 5
    assert len(service.store.list_model_calls()) == calls_before


def test_tampered_bytes_name_hash_mismatch_and_break_verify(tmp_path):
    service = _fresh_service(tmp_path)
    service.generate_dataset()
    service.generate_product_images()
    genuine = service.diagnose_listing_quality()
    assert service.listing.verify(genuine) == []
    facts = {f.payload["sku"]: f
             for f in service.store.list_facts(IMAGE_KIND)}
    dinner = facts["HW-DIN-001"].payload
    throw = facts["HW-THR-002"].payload
    service.objects.put(
        dinner["object_key"], service.objects.get(throw["object_key"]))
    assert service.listing.verify(genuine) != []
    after = service.diagnose_listing_quality()
    named = {(f["class"], f["sku"]) for f in after.payload["findings"]}
    assert ("hash_mismatch", "HW-DIN-001") in named
    assert ("vision_mismatch", "HW-DIN-001") not in named
    assert service.listing.verify(after) == []


def test_unlabelled_image_is_unjudged_never_passed(tmp_path):
    service = _fresh_service(tmp_path)
    outcome = service.ingest_product_image(
        sku="HW-DIN-001", filename="hero.png",
        data=write_png(1200, 900, (10, 20, 30), subject_class=None),
        declared_class="stoneware_dinner_set",
        source_system=generator.SOURCE_STUDIO, as_of="2026-01-04")
    assert outcome.status == "accepted"
    artefact = service.diagnose_listing_quality()
    assert {"sku": "HW-DIN-001", "reason": "vision_unlabelled"} in (
        artefact.payload["unjudged"])
    assert not any(
        f["sku"] == "HW-DIN-001" and f["class"] == "vision_mismatch"
        for f in artefact.payload["findings"])
    assert service.listing.verify(artefact) == []


# -- access -------------------------------------------------------------------


def test_listing_reads_follow_the_access_pack(service):
    service.generate_product_images()
    artefact = service.diagnose_listing_quality()
    viewer = Caller("Vic", "viewer")
    bookkeeper = Caller("Bea", "bookkeeper")
    assert service.read_latest_artefact(
        "diagnosis_listing_quality", viewer) is None
    assert service.read_latest_artefact(
        "diagnosis_listing_quality", bookkeeper) == artefact
    judgement = service.store.list_artefacts("image_vision_judgement")[0]
    assert service.read_artefact(judgement.artefact_id, viewer) is None
    assert service.read_artefact(
        judgement.artefact_id, Caller("Ola", "operator")) == judgement


# -- the hosted vision shape, stub-tested -------------------------------------


def test_meta_muse_vision_shape_against_a_stub(store, tmp_path):
    captured = {}

    def stub_transport(url, headers, body):
        captured.update(url=url, headers=headers, body=body)
        return {
            "choices": [{"message": {
                "content": '{"subject_class": "ceramic_vase"}'}}],
            "usage": {"prompt_tokens": 1200, "completion_tokens": 6},
        }

    inner = MetaMuseModelPort(
        api_key="test-key", tier="standard", transport=stub_transport)
    port = GovernedModelPort(inner, store)
    data = _healthy_png("ceramic_vase")
    reply = port.analyse_image(
        "vision-stub", "system", "what does this show?", data, "png")
    assert reply.text == '{"subject_class": "ceramic_vase"}'
    content = captured["body"]["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": "what does this show?"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith(
        "data:image/png;base64,")
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    calls = store.list_model_calls("vision-stub")
    assert len(calls) == 1
    assert calls[0].tier == "standard"
    assert (calls[0].input_tokens, calls[0].output_tokens) == (1200, 6)
    assert port.reconcile("vision-stub") == []
    contexts = [a for a in store.list_artefacts("context_assembly")
                if a.payload["call_id"] == calls[0].call_id]
    assert len(contexts) == 1


def test_meta_muse_vision_fails_closed_without_a_key(monkeypatch):
    monkeypatch.delenv("META_MODEL_API_KEY", raising=False)
    with pytest.raises(ProfileNotConfigured):
        MetaMuseModelPort()
