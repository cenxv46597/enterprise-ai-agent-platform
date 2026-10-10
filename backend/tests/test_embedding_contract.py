"""验证模型边界，阻止错误向量进入索引；此处数据只用于契约测试。"""

from dataclasses import FrozenInstanceError

import pytest

from app.providers.embeddings import (
    EmbeddingError,
    EmbeddingProfile,
    validate_embeddings,
)


def test_profile_normalizes_identifiers_and_cannot_be_changed() -> None:
    profile = EmbeddingProfile(provider="  example-provider  ", model=" example-model ", dimensions=16000)
    assert profile.provider == "example-provider"
    assert profile.model == "example-model"
    assert profile.dimensions == 16000
    with pytest.raises(FrozenInstanceError):
        profile.dimensions = 1024


@pytest.mark.parametrize(
    ("provider", "model"),
    [("", "model"), (" \t", "model"), (None, "model"), ("provider", ""), ("provider", 1)],
)
def test_profile_rejects_missing_or_nonstring_model_identity(provider, model) -> None:
    with pytest.raises((TypeError, ValueError)):
        EmbeddingProfile(provider=provider, model=model, dimensions=1024)


@pytest.mark.parametrize("dimensions", [0, -1, True, 1.5, 16001])
def test_profile_dimensions_require_a_supported_positive_integer(dimensions) -> None:
    with pytest.raises((TypeError, ValueError)):
        EmbeddingProfile(provider="provider", model="model", dimensions=dimensions)


def test_validation_returns_float_copies_without_mutating_or_aliasing_input() -> None:
    source = [[1, -2], [3, 4.5]]
    result = validate_embeddings(source, expected_count=2, dimensions=2)
    assert result == [[1.0, -2.0], [3.0, 4.5]]
    assert result is not source
    assert all(copy is not original for copy, original in zip(result, source))
    assert all(type(value) is float for vector in result for value in vector)
    assert source == [[1, -2], [3, 4.5]]

    result[0][0] = 99.0
    assert source[0][0] == 1
    source[1][0] = 88
    assert result[1][0] == 3.0


@pytest.mark.parametrize(
    ("vectors", "expected_count", "dimensions"),
    [
        ([[1, 2]], 2, 2),
        ([[1, 2], [3, 4]], 1, 2),
        ([[1, 2], [3]], 2, 2),
        ([[1, 2], [3, 4, 5]], 2, 2),
    ],
)
def test_response_shape_must_match_batch_count_and_every_vector_dimension(
    vectors, expected_count: int, dimensions: int
) -> None:
    with pytest.raises(EmbeddingError):
        validate_embeddings(vectors, expected_count=expected_count, dimensions=dimensions)


@pytest.mark.parametrize(
    "invalid_value",
    [
        True, "1.5", float("nan"), float("inf"), float("-inf"),
        3.4028237e38, -3.4028237e38,
        pytest.param(10 ** 1000, id="integer_float_conversion_overflow"),
    ],
)
def test_invalid_coordinates_are_rejected_before_storage(invalid_value) -> None:
    # 第二个坐标非零，避免错误坐标被“零向量”规则偶然挡住。
    with pytest.raises(EmbeddingError):
        validate_embeddings([[invalid_value, 2.0]], expected_count=1, dimensions=2)


def test_zero_vector_is_rejected_for_later_cosine_comparison() -> None:
    with pytest.raises(EmbeddingError):
        validate_embeddings([[0.0, -0.0]], expected_count=1, dimensions=2)


def test_nonzero_coordinates_that_all_underflow_float32_are_rejected() -> None:
    with pytest.raises(EmbeddingError):
        validate_embeddings([[1e-100, -1e-100]], expected_count=1, dimensions=2)


def test_signed_float32_boundary_is_valid_and_tuple_input_is_supported() -> None:
    maximum = 3.4028234663852886e38
    result = validate_embeddings(((maximum, -maximum),), expected_count=1, dimensions=2)
    assert result == [[maximum, -maximum]]
