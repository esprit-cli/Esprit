from esprit.llm import memory_compressor as mc


def test_resolve_model_for_counting_maps_esprit_alias() -> None:
    assert mc._resolve_model_for_counting("esprit/default") == "anthropic/claude-3-5-haiku-latest"


def test_resolve_model_for_counting_maps_bedrock_alias() -> None:
    assert (
        mc._resolve_model_for_counting("bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0")
        == "anthropic/claude-3-5-haiku-latest"
    )
