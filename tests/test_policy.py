from metasift.models import CleanMode, MetadataEntry
from metasift.policy import should_remove
from metasift.signatures import classify, classify_evidence, safe_preview


def entry(**kwargs):
    defaults = dict(key="x", category="metadata", source="test", size=1)
    defaults.update(kwargs)
    return MetadataEntry(**defaults)


def test_classification_levels():
    ai, privacy, provenance = classify("parameters", "Stable Diffusion")
    assert ai and not privacy and not provenance
    result = classify_evidence("digitalSourceType", "trainedAlgorithmicMedia")
    assert result.ai_related and result.provenance_related and result.confidence == "confirmed"
    result = classify_evidence("prompt", "owl")
    assert result.category == "workflow" and result.confidence == "possible"


def test_classify_privacy_and_preview():
    ai, privacy, provenance = classify("GPS Latitude", "12.3")
    assert privacy and not ai and not provenance
    assert safe_preview("a\n" + "b" * 200, 10).endswith("…")


def test_policy_modes_and_overrides():
    workflow = entry(key="prompt", path="x.prompt", ai_related=True)
    privacy = entry(key="creator", privacy_related=True)
    provenance = entry(key="C2PA", provenance_related=True)
    technical = entry(key="Orientation", rendering_required=True)

    assert should_remove(workflow, CleanMode.WORKFLOW)
    assert not should_remove(provenance, CleanMode.WORKFLOW)
    assert should_remove(provenance, CleanMode.PROVENANCE)
    assert should_remove(privacy, CleanMode.SHARE_SAFE)
    assert should_remove(workflow, CleanMode.SHARE_SAFE)
    assert not should_remove(provenance, CleanMode.SHARE_SAFE)
    assert not should_remove(technical, CleanMode.METADATA_MAX)
    assert should_remove(technical, CleanMode.CUSTOM, remove_keys=("Orientation",))
    assert not should_remove(workflow, CleanMode.AI, keep_keys=("x.prompt",))
    assert should_remove(privacy, CleanMode.CUSTOM, remove_keys=("creator",))
