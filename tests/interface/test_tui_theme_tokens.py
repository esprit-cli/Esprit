from esprit.interface.theme_tokens import (
    DEFAULT_THEME_ID,
    REQUIRED_SEMANTIC_KEYS,
    SUPPORTED_THEME_IDS,
    get_theme_tokens,
    normalize_theme_id,
)


def test_all_supported_themes_resolve() -> None:
    assert set(SUPPORTED_THEME_IDS) == {"esprit", "ember", "matrix", "glacier", "crt"}

    for theme_id in SUPPORTED_THEME_IDS:
        tokens = get_theme_tokens(theme_id)
        assert isinstance(tokens, dict)
        assert tokens


def test_each_theme_contains_required_semantic_keys() -> None:
    for theme_id in SUPPORTED_THEME_IDS:
        tokens = get_theme_tokens(theme_id)
        for key in REQUIRED_SEMANTIC_KEYS:
            assert key in tokens
            assert isinstance(tokens[key], str)
            assert tokens[key]


def test_theme_normalization_falls_back_to_default() -> None:
    assert normalize_theme_id("invalid-theme") == DEFAULT_THEME_ID
