from app.server import (
    _build_preview_payload_cache_key,
    _build_quantized_render_key
)


def test_preview_cursor_state_change():
    base = {
        "compose_cursor": True,
        "cursor_col": 10,
        "cursor_row": 5,
        "cursor_state": "crosshair"
    }

    changed = {
        **base,
        "cursor_state": "guidelines"
    }

    assert _build_preview_payload_cache_key(base, model_index=0, pixel_width=60, pixel_height=40) != _build_preview_payload_cache_key(
        changed,
        model_index=0,
        pixel_width=60,
        pixel_height=40
    )


def test_preview_cursor_col_change():
    base = {
        "compose_cursor": True,
        "cursor_col": 20,
        "cursor_row": 15,
        "cursor_state": "guidelines"
    }

    changed = {
        **base,
        "cursor_col": 25,
    }

    assert _build_preview_payload_cache_key(base, model_index=1, pixel_width=60, pixel_height=40) != _build_preview_payload_cache_key(
        changed,
        model_index=1,
        pixel_width=60,
        pixel_height=40
    )


def test_preview_cursor_row_change():
    base = {
        "compose_cursor": True,
        "cursor_col": 20,
        "cursor_row": 15,
        "cursor_state": "guidelines"
    }

    changed = {
        **base,
        "cursor_row": 10,
    }

    assert _build_preview_payload_cache_key(base, model_index=1, pixel_width=60, pixel_height=40) != _build_preview_payload_cache_key(
        changed,
        model_index=1,
        pixel_width=60,
        pixel_height=40
    )


def test_quant_cursor_fields():
    key1 = {
        "compose_cursor": True,
        "cursor_col": 30,
        "cursor_row": 25,
        "cursor_state": "crosshair"
    }

    key2 = {
        "compose_cursor": True,
        "cursor_col": 30,
        "cursor_row": 25,
        "cursor_state": "horizontal-line"
    }

    assert _build_quantized_render_key(key1, model_index=2) != _build_quantized_render_key(
        key2,
        model_index=2
    )
