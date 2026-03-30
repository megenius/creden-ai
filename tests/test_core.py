"""
Comprehensive unit tests for creden-ai core logic.

Covers:
  - setup_db.py  : parse_thai_number(), parse_ratio(), map_company_data()
  - gemini_client.py : _fallback_embedding() determinism, get_embedding() fallback
  - vec_search.py    : _serialize_embedding()
  - mcp_server.py    : fmt_currency(), fmt_ratio(), fmt_company_card(), _lookup() routing
  - app.py           : Flask API endpoints via test client

Run:
    cd /Users/boy/cowork/creden/v2/creden-ai
    venv/bin/pytest tests/test_core.py -v
"""

import importlib
import struct
import sys
import os
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path helpers — make the project root importable
# ---------------------------------------------------------------------------

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ---------------------------------------------------------------------------
# Stubs for heavy optional dependencies that may not be present in CI
# ---------------------------------------------------------------------------

def _stub_module(name: str, **attrs):
    """Register a minimal stub module so `import <name>` succeeds."""
    if name not in sys.modules:
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod
    return sys.modules[name]


# Stub out mcp and its sub-packages before importing mcp_server
_stub_module("mcp")
_stub_module("mcp.server")
_stub_module("mcp.server.fastmcp", FastMCP=MagicMock())

# Stub pb_client so mcp_server and app load without a live PocketBase
_pb_client_stub = _stub_module(
    "pb_client",
    pb_list=MagicMock(return_value=[]),
    pb_list_all=MagicMock(return_value=[]),
    pb_get=MagicMock(return_value=None),
    PB_URL="http://127.0.0.1:8090",
)


# ---------------------------------------------------------------------------
# Lazy module imports (after stubs are in place)
# ---------------------------------------------------------------------------

from setup_db import parse_thai_number, parse_ratio, map_company_data  # noqa: E402
from gemini_client import _fallback_embedding, get_embedding, EMBED_DIM  # noqa: E402
from vec_search import _serialize_embedding  # noqa: E402


# ============================================================================
# 1. parse_thai_number()
# ============================================================================

class TestParseThaiNumber:
    """Tests for parse_thai_number() — Thai financial string → float (ล้านบาท)."""

    # ── None / empty / sentinel values ──────────────────────────────────────

    def test_none_returns_none(self):
        assert parse_thai_number(None) is None

    def test_empty_string_returns_none(self):
        assert parse_thai_number("") is None

    def test_whitespace_only_returns_none(self):
        assert parse_thai_number("   ") is None

    def test_dash_returns_none(self):
        assert parse_thai_number("-") is None

    def test_na_string_returns_none(self):
        assert parse_thai_number("N/A") is None

    # ── Numeric pass-through ─────────────────────────────────────────────────

    def test_int_passthrough(self):
        assert parse_thai_number(100) == 100.0

    def test_float_passthrough(self):
        assert parse_thai_number(3.14) == 3.14

    def test_zero_int_passthrough(self):
        assert parse_thai_number(0) == 0.0

    def test_negative_float_passthrough(self):
        assert parse_thai_number(-95.6) == -95.6

    # ── ล้านบาท (million baht) ── values returned as-is ──────────────────────

    def test_large_number_with_comma_million(self):
        """'15,600 ล้านบาท' → 15600.0  (Lazada registered capital)"""
        assert parse_thai_number("15,600 ล้านบาท") == 15600.0

    def test_negative_million_baht(self):
        """'-95.6 ล้านบาท' → -95.6  (Lazada negative equity)"""
        assert parse_thai_number("-95.6 ล้านบาท") == -95.6

    def test_positive_decimal_million(self):
        """'1.4 ล้านบาท' → 1.4  (Creden capital)"""
        assert parse_thai_number("1.4 ล้านบาท") == 1.4

    def test_whole_million_no_comma(self):
        """'604.6 ล้านบาท' → 604.6"""
        assert parse_thai_number("604.6 ล้านบาท") == 604.6

    def test_very_large_million(self):
        """'737,535 ล้านบาท' → 737535.0  (PTT OR revenue)"""
        assert parse_thai_number("737,535 ล้านบาท") == 737535.0

    def test_million_baht_with_leading_whitespace(self):
        assert parse_thai_number("  120,000 ล้านบาท") == 120000.0

    # ── บาท (baht) ── values divided by 1,000,000 ────────────────────────────

    def test_zero_baht(self):
        """'0 บาท' → 0.0  (Lazada tax / Creden inventory)"""
        result = parse_thai_number("0 บาท")
        assert result == 0.0

    def test_plain_baht_converts_to_millions(self):
        """'158,832 บาท' → 0.158832  (Creden fixed assets)"""
        result = parse_thai_number("158,832 บาท")
        assert abs(result - 0.158832) < 1e-9

    def test_small_baht_converts_to_millions(self):
        """'120,959 บาท' → 0.120959"""
        result = parse_thai_number("120,959 บาท")
        assert abs(result - 0.120959) < 1e-9

    def test_baht_not_million_does_not_contain_million_word(self):
        """'500 บาท' → 500 / 1_000_000 = 0.0005 (NOT treated as ล้านบาท)."""
        result = parse_thai_number("500 บาท")
        assert abs(result - 0.0005) < 1e-9

    # ── Edge cases ───────────────────────────────────────────────────────────

    def test_number_without_unit_treated_as_million(self):
        """A bare number string (no unit) passes through as float directly."""
        result = parse_thai_number("100")
        assert result == 100.0

    def test_garbage_string_returns_none(self):
        assert parse_thai_number("abc") is None

    def test_string_with_only_commas_returns_none(self):
        assert parse_thai_number(",,,") is None

    def test_returns_float_type(self):
        result = parse_thai_number("1 ล้านบาท")
        assert isinstance(result, float)

    def test_negative_zero_baht(self):
        """'-0 บาท' — edge: negative zero rounds to 0.0"""
        result = parse_thai_number("-0 บาท")
        assert result == 0.0

    def test_large_comma_separated_baht(self):
        """'1,000,000 บาท' → 1.0 (exactly one million baht = 1 ล้านบาท)"""
        result = parse_thai_number("1,000,000 บาท")
        assert abs(result - 1.0) < 1e-9


# ============================================================================
# 2. parse_ratio()
# ============================================================================

class TestParseRatio:
    """Tests for parse_ratio() — ratio/percentage values returned as-is."""

    # ── None / empty ─────────────────────────────────────────────────────────

    def test_none_returns_none(self):
        assert parse_ratio(None) is None

    def test_empty_string_returns_none(self):
        assert parse_ratio("") is None

    def test_whitespace_string_returns_none(self):
        assert parse_ratio("   ") is None

    # ── Numeric pass-through ─────────────────────────────────────────────────

    def test_int_passthrough(self):
        assert parse_ratio(5) == 5.0

    def test_float_passthrough(self):
        assert parse_ratio(2.01) == 2.01

    def test_zero_passthrough(self):
        assert parse_ratio(0) == 0.0

    # ── String conversions ───────────────────────────────────────────────────

    def test_negative_string(self):
        """-122.5 as string (Lazada D/E ratio with negative equity)"""
        assert parse_ratio("-122.5") == -122.5

    def test_positive_string(self):
        """'2.01' (Creden D/E ratio)"""
        assert parse_ratio("2.01") == 2.01

    def test_string_with_commas(self):
        """'1,500.5' → 1500.5"""
        assert parse_ratio("1,500.5") == 1500.5

    def test_integer_string(self):
        """'2' (PTT OR current ratio stored as '2')"""
        assert parse_ratio("2") == 2.0

    def test_zero_string(self):
        """'0' (Creden gross_profit_margin)"""
        assert parse_ratio("0") == 0.0

    def test_percentage_like_string(self):
        """'4.92' (Lazada ROA)"""
        assert parse_ratio("4.92") == 4.92

    def test_negative_percentage(self):
        """-151.96 (Lazada ROE)"""
        assert parse_ratio("-151.96") == -151.96

    def test_garbage_string_returns_none(self):
        assert parse_ratio("abc") is None

    def test_returns_float_type(self):
        result = parse_ratio("1.75")
        assert isinstance(result, float)

    def test_whitespace_stripped(self):
        assert parse_ratio("  3.14  ") == 3.14


# ============================================================================
# 3. map_company_data()
# ============================================================================

# Representative raw record matching the JSON shape in _all_companies.json
LAZADA_RAW = {
    "id": "0105555040244",
    "name": "ลาซาด้า จำกัด",
    "data": {
        "เลขนิติบุคคล": "0105555040244",
        "ชื่อบริษัท (ไทย)": "ลาซาด้า จำกัด",
        "ชื่อบริษัท (อังกฤษ)": "LAZADA LTD.",
        "ประเภทนิติบุคคล": "บริษัทจำกัด",
        "สถานะ": "ยังดำเนินกิจการอยู่",
        "ประเภทธุรกิจ": "การขายปลีกทางอินเทอร์เน็ต",
        "หมวดธุรกิจ": "การขายส่งและการขายปลีก",
        "จังหวัด": "กรุงเทพมหานคร",
        "ภูมิภาค": "กรุงเทพฯ และปริมณฑล",
        "ทุนจดทะเบียน": "15,600 ล้านบาท",
        "สินทรัพย์รวม": "11,610 ล้านบาท",
        "หนี้สินรวม": "11,706 ล้านบาท",
        "ส่วนของผู้ถือหุ้น": "-95.6 ล้านบาท",
        "รายได้รวม": "21,471 ล้านบาท",
        "กำไร(ขาดทุน)สุทธิ": "604.6 ล้านบาท",
        "หนี้สินรวม/ส่วนของผู้ถือหุ้น (D/E) (เท่า)": "-122.5",
        "อัตรากำไรสุทธิ (%)": "2.82",
        "ผลตอบแทนจากสินทรัพย์ ROA (%)": "4.92",
        "ผลตอบแทนจากส่วนของผู้ถือหุ้น ROE (%)": "-151.96",
        "อัตราส่วนทุนหมุนเวียน (เท่า)": "0.97",
        "COMPANY_SIZE": "บริษัทขนาดใหญ่",
        "REG_DATE_DISPLAY": "14 มีนาคม พ.ศ. 2555 (ค.ศ. 2012)",
        "FISCAL_YEAR_DISPLAY": "พ.ศ. 2566 (ค.ศ. 2023)",
        "กรรมการ": ["นายอัลเบิร์ต หยุนฉวน หลิว", "นางสุกัญญา รังสิคุต"],
        "_FLAGS": ["ส่วนของผู้ถือหุ้นติดลบ (-95.6 ล้านบาท)"],
    },
}

CREDEN_RAW = {
    "id": "0105560098166",
    "name": "ครีเดน เอเชีย จำกัด",
    "data": {
        "เลขนิติบุคคล": "0105560098166",
        "ชื่อบริษัท (ไทย)": "ครีเดน เอเชีย จำกัด",
        "ชื่อบริษัท (อังกฤษ)": "CREDEN ASIA COMPANY LIMITED",
        "ทุนจดทะเบียน": "1.4 ล้านบาท",
        "ที่ดิน อาคารและอุปกรณ์": "158,832 บาท",
        "สินค้าคงเหลือ": "0 บาท",
        "กำไร(ขาดทุน)ขั้นต้น": "0 บาท",
        "หนี้สินไม่หมุนเวียน": "0 บาท",
        "สินทรัพย์รวม": "14.8 ล้านบาท",
        "กำไร(ขาดทุน)สุทธิ": "1.3 ล้านบาท",
        "อัตรากำไรขั้นต้น (%)": "0",
        "COMPANY_SIZE": "บริษัทขนาดเล็ก (SME)",
        "กรรมการ": ["นายภาวุธ พงษ์วิทยภานุ"],
        "_FLAGS": ["อัตราส่วนทุนหมุนเวียน 0.72 เท่า"],
    },
}


class TestMapCompanyData:
    """Tests for map_company_data() — raw JSON dict → PocketBase record dict."""

    def test_returns_dict(self):
        result = map_company_data(LAZADA_RAW)
        assert isinstance(result, dict)

    # ── Text field mapping ───────────────────────────────────────────────────

    def test_juristic_id_mapped(self):
        result = map_company_data(LAZADA_RAW)
        assert result["juristic_id"] == "0105555040244"

    def test_name_th_mapped(self):
        result = map_company_data(LAZADA_RAW)
        assert result["name_th"] == "ลาซาด้า จำกัด"

    def test_name_en_mapped(self):
        result = map_company_data(LAZADA_RAW)
        assert result["name_en"] == "LAZADA LTD."

    def test_entity_type_mapped(self):
        result = map_company_data(LAZADA_RAW)
        assert result["entity_type"] == "บริษัทจำกัด"

    def test_company_size_mapped(self):
        result = map_company_data(LAZADA_RAW)
        assert result["company_size"] == "บริษัทขนาดใหญ่"

    def test_reg_date_display_mapped(self):
        result = map_company_data(LAZADA_RAW)
        assert "2555" in result["reg_date_display"]

    def test_fiscal_year_display_mapped(self):
        result = map_company_data(LAZADA_RAW)
        assert "2566" in result["fiscal_year_display"]

    # ── Currency field parsing ────────────────────────────────────────────────

    def test_registered_capital_parsed(self):
        result = map_company_data(LAZADA_RAW)
        assert result["registered_capital"] == 15600.0

    def test_total_assets_parsed(self):
        result = map_company_data(LAZADA_RAW)
        assert result["total_assets"] == 11610.0

    def test_total_liabilities_parsed(self):
        result = map_company_data(LAZADA_RAW)
        assert result["total_liabilities"] == 11706.0

    def test_negative_equity_parsed(self):
        """Lazada: '-95.6 ล้านบาท' → -95.6"""
        result = map_company_data(LAZADA_RAW)
        assert result["equity"] == -95.6

    def test_total_revenue_parsed(self):
        result = map_company_data(LAZADA_RAW)
        assert result["total_revenue"] == 21471.0

    def test_net_profit_parsed(self):
        result = map_company_data(LAZADA_RAW)
        assert result["net_profit"] == 604.6

    # ── Baht (not ล้านบาท) field parsing ─────────────────────────────────────

    def test_zero_baht_field_mapped_as_zero(self):
        """'0 บาท' for กำไร(ขาดทุน)ขั้นต้น → 0.0 (NOT absent)"""
        result = map_company_data(CREDEN_RAW)
        # 0.0 / 1_000_000 = 0.0
        assert result["gross_profit"] == 0.0

    def test_baht_field_converted_to_millions(self):
        """'158,832 บาท' is NOT in FIELD_MAP (it's ที่ดิน อาคารและอุปกรณ์, not mapped).
        But '0 บาท' fields that ARE mapped should be 0.0."""
        result = map_company_data(CREDEN_RAW)
        # สินค้าคงเหลือ is NOT in FIELD_MAP; check a field that is in the map:
        # กำไร(ขาดทุน)ขั้นต้น → gross_profit
        assert "gross_profit" in result
        assert result["gross_profit"] == 0.0

    def test_small_capital_creden(self):
        """'1.4 ล้านบาท' → 1.4"""
        result = map_company_data(CREDEN_RAW)
        assert result["registered_capital"] == 1.4

    # ── Ratio field parsing ──────────────────────────────────────────────────

    def test_de_ratio_negative(self):
        """-122.5 for Lazada D/E ratio"""
        result = map_company_data(LAZADA_RAW)
        assert result["de_ratio"] == -122.5

    def test_net_profit_margin_parsed(self):
        result = map_company_data(LAZADA_RAW)
        assert abs(result["net_profit_margin"] - 2.82) < 1e-9

    def test_roa_parsed(self):
        result = map_company_data(LAZADA_RAW)
        assert abs(result["roa"] - 4.92) < 1e-9

    def test_roe_negative_parsed(self):
        result = map_company_data(LAZADA_RAW)
        assert result["roe"] == -151.96

    def test_current_ratio_parsed(self):
        result = map_company_data(LAZADA_RAW)
        assert abs(result["current_ratio"] - 0.97) < 1e-9

    def test_zero_ratio_string_parsed(self):
        """'0' for gross_profit_margin (Creden) → 0.0"""
        result = map_company_data(CREDEN_RAW)
        assert result["gross_profit_margin"] == 0.0

    # ── JSON pass-through fields ──────────────────────────────────────────────

    def test_directors_passed_through(self):
        result = map_company_data(LAZADA_RAW)
        assert result["directors"] == ["นายอัลเบิร์ต หยุนฉวน หลิว", "นางสุกัญญา รังสิคุต"]

    def test_flags_passed_through(self):
        result = map_company_data(LAZADA_RAW)
        assert isinstance(result["flags"], list)
        assert len(result["flags"]) == 1

    def test_flags_empty_list_not_in_result(self):
        """Empty list _FLAGS — the field is present in raw but has falsy [] value.
        map_company_data skips None values; an empty list is not None, so it IS included."""
        raw = {"data": {"เลขนิติบุคคล": "9999999999999", "ชื่อบริษัท (ไทย)": "Test", "_FLAGS": []}}
        result = map_company_data(raw)
        # An empty list is not None, so it passes through
        assert result.get("flags") == []

    # ── Missing / partial data ───────────────────────────────────────────────

    def test_empty_data_returns_empty_dict(self):
        result = map_company_data({"data": {}})
        assert result == {}

    def test_missing_data_key_returns_empty_dict(self):
        result = map_company_data({})
        assert result == {}

    def test_none_values_are_skipped(self):
        """Fields whose source value is None should not appear in the result."""
        raw = {"data": {"ชื่อบริษัท (ไทย)": "Test Co", "สถานะ": None}}
        result = map_company_data(raw)
        assert "status" not in result

    def test_unknown_keys_ignored(self):
        """Keys not in FIELD_MAP should be silently ignored."""
        raw = {"data": {"ชื่อบริษัท (ไทย)": "Test", "UNKNOWN_KEY": "value"}}
        result = map_company_data(raw)
        assert "UNKNOWN_KEY" not in result
        assert result.get("name_th") == "Test"

    def test_all_text_fields_are_strings(self):
        result = map_company_data(LAZADA_RAW)
        text_fields = ["juristic_id", "name_th", "name_en", "entity_type",
                       "status", "business_type", "business_category",
                       "province", "region", "company_size"]
        for field in text_fields:
            if field in result:
                assert isinstance(result[field], str), f"{field} should be str"


# ============================================================================
# 4. _fallback_embedding() and get_embedding()
# ============================================================================

class TestFallbackEmbedding:
    """Tests for gemini_client._fallback_embedding()."""

    def test_returns_list(self):
        result = _fallback_embedding("test")
        assert isinstance(result, list)

    def test_correct_dimension(self):
        result = _fallback_embedding("hello world")
        assert len(result) == EMBED_DIM  # 768

    def test_all_values_are_floats(self):
        result = _fallback_embedding("any text")
        assert all(isinstance(v, float) for v in result)

    def test_all_values_in_range(self):
        """Values should be in [-1, 1] after normalisation."""
        result = _fallback_embedding("range check")
        assert all(-1.0 <= v <= 1.0 for v in result), "all values must be in [-1, 1]"

    def test_deterministic_same_input(self):
        """Same input must produce identical output every time."""
        a = _fallback_embedding("ลาซาด้า")
        b = _fallback_embedding("ลาซาด้า")
        assert a == b

    def test_different_inputs_produce_different_embeddings(self):
        """Different strings should (with overwhelming probability) differ."""
        a = _fallback_embedding("lazada")
        b = _fallback_embedding("creden")
        assert a != b

    def test_empty_string(self):
        result = _fallback_embedding("")
        assert len(result) == EMBED_DIM

    def test_thai_text(self):
        result = _fallback_embedding("บริษัทขายปลีกทางอินเทอร์เน็ต")
        assert len(result) == EMBED_DIM

    def test_no_nan_or_inf(self):
        import math
        result = _fallback_embedding("NaN/Inf guard test")
        assert all(math.isfinite(v) for v in result)

    def test_long_text(self):
        long_text = "ข้อมูลบริษัท " * 200
        result = _fallback_embedding(long_text)
        assert len(result) == EMBED_DIM

    def test_whitespace_only(self):
        result = _fallback_embedding("   ")
        assert len(result) == EMBED_DIM


class TestGetEmbeddingFallback:
    """Tests for get_embedding() when GEMINI_API_KEY is absent or API fails."""

    def test_returns_fallback_when_no_api_key(self):
        """With no key the function should silently use _fallback_embedding."""
        import gemini_client as gc
        original_key = gc.GEMINI_API_KEY
        try:
            gc.GEMINI_API_KEY = ""
            result = get_embedding("test without key")
            assert len(result) == EMBED_DIM
        finally:
            gc.GEMINI_API_KEY = original_key

    def test_returns_fallback_on_api_exception(self):
        """When the API raises, get_embedding must fall back gracefully."""
        import gemini_client as gc
        original_key = gc.GEMINI_API_KEY
        try:
            gc.GEMINI_API_KEY = "fake-key-to-trigger-api-path"
            with patch("gemini_client.requests.post", side_effect=Exception("network error")):
                result = get_embedding("api fails")
            assert len(result) == EMBED_DIM
        finally:
            gc.GEMINI_API_KEY = original_key

    def test_returns_fallback_on_http_error(self):
        """An HTTP error response should also trigger fallback."""
        import gemini_client as gc
        original_key = gc.GEMINI_API_KEY
        try:
            gc.GEMINI_API_KEY = "fake-key"
            mock_resp = MagicMock()
            mock_resp.raise_for_status.side_effect = Exception("401 Unauthorized")
            with patch("gemini_client.requests.post", return_value=mock_resp):
                result = get_embedding("http error")
            assert len(result) == EMBED_DIM
        finally:
            gc.GEMINI_API_KEY = original_key

    def test_uses_real_api_when_key_set_and_request_succeeds(self):
        """If the API returns a valid 768-dim embedding, use it directly."""
        import gemini_client as gc
        original_key = gc.GEMINI_API_KEY
        try:
            gc.GEMINI_API_KEY = "valid-key"
            fake_values = [0.1] * EMBED_DIM
            mock_resp = MagicMock()
            mock_resp.raise_for_status.return_value = None
            mock_resp.json.return_value = {"embedding": {"values": fake_values}}
            with patch("gemini_client.requests.post", return_value=mock_resp):
                result = get_embedding("real api call")
            assert result == fake_values
        finally:
            gc.GEMINI_API_KEY = original_key


# ============================================================================
# 5. _serialize_embedding()
# ============================================================================

class TestSerializeEmbedding:
    """Tests for vec_search._serialize_embedding()."""

    def test_returns_bytes(self):
        embedding = [0.1, 0.2, 0.3]
        result = _serialize_embedding(embedding)
        assert isinstance(result, bytes)

    def test_byte_length_correct(self):
        """Each float32 is 4 bytes, so 768 floats → 3072 bytes."""
        embedding = [0.0] * 768
        result = _serialize_embedding(embedding)
        assert len(result) == 768 * 4

    def test_small_embedding_correct_bytes(self):
        embedding = [1.0, 2.0, 3.0]
        result = _serialize_embedding(embedding)
        expected = struct.pack("3f", 1.0, 2.0, 3.0)
        assert result == expected

    def test_roundtrip(self):
        """Serialised bytes can be deserialized back to the same floats."""
        original = [0.5, -0.5, 0.123456789, -0.999]
        serialized = _serialize_embedding(original)
        recovered = list(struct.unpack(f"{len(original)}f", serialized))
        for orig, rec in zip(original, recovered):
            assert abs(orig - rec) < 1e-6, f"{orig} != {rec} after roundtrip"

    def test_empty_embedding(self):
        result = _serialize_embedding([])
        assert result == b""

    def test_negative_values(self):
        embedding = [-1.0, -0.5, -0.1]
        result = _serialize_embedding(embedding)
        assert len(result) == 3 * 4

    def test_768_dim_fallback_embedding_serializable(self):
        """The 768-dim fallback embedding must survive serialization."""
        emb = _fallback_embedding("serialize me")
        blob = _serialize_embedding(emb)
        assert len(blob) == 768 * 4


# ============================================================================
# 6. mcp_server helpers: fmt_currency(), fmt_ratio(), fmt_company_card()
# ============================================================================

# Import the helper functions directly (FastMCP is already stubbed)
import mcp_server as _mcp_mod

fmt_currency = _mcp_mod.fmt_currency
fmt_ratio = _mcp_mod.fmt_ratio
fmt_company_card = _mcp_mod.fmt_company_card
_lookup = _mcp_mod._lookup


class TestFmtCurrency:
    """Tests for fmt_currency()."""

    def test_none_returns_na(self):
        assert fmt_currency(None) == "N/A"

    def test_zero(self):
        result = fmt_currency(0)
        assert result == "0.00 ล้านบาท"

    def test_small_positive(self):
        """Values < 1000 use 2 decimal places."""
        result = fmt_currency(1.3)
        assert result == "1.30 ล้านบาท"

    def test_large_positive_no_decimal(self):
        """Values >= 1000 use 0 decimal places with comma separator."""
        result = fmt_currency(21471.0)
        assert result == "21,471 ล้านบาท"

    def test_large_number_with_comma(self):
        result = fmt_currency(737535.0)
        assert "737,535" in result

    def test_negative_small(self):
        result = fmt_currency(-95.6)
        assert result == "-95.60 ล้านบาท"

    def test_negative_large(self):
        result = fmt_currency(-11706.0)
        assert "-11,706" in result

    def test_exact_1000_boundary_uses_integer_format(self):
        """1000 is >= 1000, so should use the integer-formatted branch."""
        result = fmt_currency(1000.0)
        assert result == "1,000 ล้านบาท"

    def test_just_below_1000_uses_decimal_format(self):
        """999.9 < 1000 → 2 decimal places."""
        result = fmt_currency(999.9)
        assert result == "999.90 ล้านบาท"

    def test_unit_suffix_present(self):
        assert "ล้านบาท" in fmt_currency(100.0)

    def test_negative_large_abs_value(self):
        """abs(-1000) == 1000 → integer format."""
        result = fmt_currency(-1000.0)
        assert result == "-1,000 ล้านบาท"


class TestFmtRatio:
    """Tests for fmt_ratio()."""

    def test_none_returns_na(self):
        assert fmt_ratio(None) == "N/A"

    def test_none_custom_unit(self):
        assert fmt_ratio(None, " เท่า") == "N/A"

    def test_default_percent_unit(self):
        result = fmt_ratio(4.92)
        assert result == "4.92%"

    def test_custom_unit(self):
        result = fmt_ratio(0.97, " เท่า")
        assert result == "0.97 เท่า"

    def test_negative_value(self):
        result = fmt_ratio(-122.5)
        assert result == "-122.50%"

    def test_zero(self):
        result = fmt_ratio(0.0)
        assert result == "0.00%"

    def test_two_decimal_places(self):
        result = fmt_ratio(3.1)
        assert result == "3.10%"

    def test_large_number_with_comma(self):
        result = fmt_ratio(1234.5)
        assert "1,234.50" in result


class TestFmtCompanyCard:
    """Tests for fmt_company_card()."""

    LAZADA_COMPANY = {
        "name_th": "ลาซาด้า จำกัด",
        "name_en": "LAZADA LTD.",
        "company_size": "บริษัทขนาดใหญ่",
        "total_revenue": 21471.0,
        "net_profit": 604.6,
    }

    CREDEN_COMPANY = {
        "name_th": "ครีเดน เอเชีย จำกัด",
        "name_en": "CREDEN ASIA COMPANY LIMITED",
        "company_size": "บริษัทขนาดเล็ก (SME)",
        "total_revenue": 16.9,
        "net_profit": 1.3,
    }

    def test_contains_company_name(self):
        result = fmt_company_card(self.LAZADA_COMPANY)
        assert "ลาซาด้า จำกัด" in result

    def test_contains_english_name(self):
        result = fmt_company_card(self.LAZADA_COMPANY)
        assert "LAZADA LTD." in result

    def test_large_company_uses_building_emoji(self):
        result = fmt_company_card(self.LAZADA_COMPANY)
        assert "🏢" in result

    def test_sme_uses_house_emoji(self):
        result = fmt_company_card(self.CREDEN_COMPANY)
        assert "🏠" in result

    def test_contains_revenue(self):
        result = fmt_company_card(self.LAZADA_COMPANY)
        assert "รายได้" in result

    def test_contains_profit_when_present(self):
        result = fmt_company_card(self.LAZADA_COMPANY)
        assert "กำไร" in result

    def test_no_profit_field_when_absent(self):
        company = {"name_th": "Test", "name_en": "", "company_size": "", "total_revenue": 10.0}
        result = fmt_company_card(company)
        assert "กำไร" not in result

    def test_with_index(self):
        result = fmt_company_card(self.LAZADA_COMPANY, idx=3)
        assert result.startswith("3. ")

    def test_without_index_no_prefix(self):
        result = fmt_company_card(self.LAZADA_COMPANY, idx=None)
        # Should not start with a number and dot
        assert not result.startswith("1. ")

    def test_missing_fields_handled_gracefully(self):
        """Empty dict should not raise — returns N/A placeholders."""
        result = fmt_company_card({})
        assert "N/A" in result

    def test_none_revenue_shows_na(self):
        company = {"name_th": "Test", "name_en": "", "total_revenue": None}
        result = fmt_company_card(company)
        assert "N/A" in result


# ============================================================================
# 7. _lookup() routing logic
# ============================================================================

class TestLookup:
    """Tests for _lookup() — routes by juristic_id vs. name query."""

    def test_13_digit_number_uses_juristic_filter(self):
        """A 13-digit numeric string must call pb_list with juristic_id filter."""
        with patch("mcp_server.pb_list", return_value=[]) as mock_pb:
            _lookup("0105555040244")
        call_kwargs = mock_pb.call_args
        filter_arg = call_kwargs[1]["filter"] if call_kwargs[1] else call_kwargs[0][1]
        assert "juristic_id" in filter_arg
        assert "0105555040244" in filter_arg

    def test_name_query_uses_name_filter(self):
        """A non-numeric string must search by name."""
        with patch("mcp_server.pb_list", return_value=[]) as mock_pb:
            _lookup("ลาซาด้า")
        call_kwargs = mock_pb.call_args
        filter_arg = call_kwargs[1]["filter"] if call_kwargs[1] else call_kwargs[0][1]
        assert "name_th" in filter_arg

    def test_juristic_id_with_spaces_normalised(self):
        """Spaces inside a 13-digit ID should be stripped before lookup."""
        with patch("mcp_server.pb_list", return_value=[]) as mock_pb:
            _lookup("0105 555 040244")
        call_kwargs = mock_pb.call_args
        filter_arg = call_kwargs[1]["filter"] if call_kwargs[1] else call_kwargs[0][1]
        assert "juristic_id" in filter_arg

    def test_12_digit_number_uses_name_filter(self):
        """12 digits is NOT a valid juristic_id — should use name search."""
        with patch("mcp_server.pb_list", return_value=[]) as mock_pb:
            _lookup("010555504024")  # 12 digits
        call_kwargs = mock_pb.call_args
        filter_arg = call_kwargs[1]["filter"] if call_kwargs[1] else call_kwargs[0][1]
        assert "name_th" in filter_arg

    def test_returns_list(self):
        with patch("mcp_server.pb_list", return_value=[{"id": "x"}]):
            result = _lookup("ลาซาด้า")
        assert isinstance(result, list)

    def test_empty_result_returns_empty_list(self):
        with patch("mcp_server.pb_list", return_value=[]):
            result = _lookup("nonexistent")
        assert result == []


# ============================================================================
# 8. Flask API endpoints
# ============================================================================

class TestFlaskApi:
    """Integration-style tests for Flask endpoints using the test client.

    All PocketBase calls are patched so no live server is needed.
    """

    @pytest.fixture(autouse=True)
    def client(self):
        """Create a Flask test client with a clean app context."""
        import app as flask_app_module
        flask_app_module.app.config["TESTING"] = True
        self.client = flask_app_module.app.test_client()
        return self.client

    # ── /health ──────────────────────────────────────────────────────────────

    def test_health_returns_200(self):
        resp = self.client.get("/health")
        assert resp.status_code == 200

    def test_health_returns_ok_status(self):
        resp = self.client.get("/health")
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_health_contains_app_name(self):
        resp = self.client.get("/health")
        data = resp.get_json()
        assert data["app"] == "creden-ai"

    def test_health_contains_time(self):
        resp = self.client.get("/health")
        data = resp.get_json()
        assert "time" in data

    # ── /api/companies ───────────────────────────────────────────────────────

    def test_list_companies_returns_200(self):
        with patch("app.pb_list", return_value=[]):
            resp = self.client.get("/api/companies")
        assert resp.status_code == 200

    def test_list_companies_returns_items_and_total(self):
        companies = [{"id": "1", "name_th": "Test Co"}]
        with patch("app.pb_list", return_value=companies):
            resp = self.client.get("/api/companies")
        data = resp.get_json()
        assert "items" in data
        assert "total" in data
        assert data["total"] == 1

    def test_list_companies_empty_result(self):
        with patch("app.pb_list", return_value=[]):
            resp = self.client.get("/api/companies")
        data = resp.get_json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_list_companies_province_filter(self):
        """Province query param must be passed along to pb_list."""
        with patch("app.pb_list", return_value=[]) as mock_pb:
            self.client.get("/api/companies?province=กรุงเทพมหานคร")
        call_kwargs = mock_pb.call_args[1]
        assert "กรุงเทพมหานคร" in call_kwargs.get("filter", "")

    def test_list_companies_company_size_filter(self):
        with patch("app.pb_list", return_value=[]) as mock_pb:
            self.client.get("/api/companies?company_size=SME")
        call_kwargs = mock_pb.call_args[1]
        assert "SME" in call_kwargs.get("filter", "")

    def test_list_companies_invalid_limit_falls_back_to_50(self):
        """Non-integer limit value should not crash — defaults to 50."""
        with patch("app.pb_list", return_value=[]):
            resp = self.client.get("/api/companies?limit=notanumber")
        assert resp.status_code == 200

    def test_list_companies_content_type_json(self):
        with patch("app.pb_list", return_value=[]):
            resp = self.client.get("/api/companies")
        assert "application/json" in resp.content_type

    # ── /api/companies/search ────────────────────────────────────────────────

    def test_search_empty_query_returns_empty(self):
        resp = self.client.get("/api/companies/search?q=")
        data = resp.get_json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_search_missing_q_returns_empty(self):
        resp = self.client.get("/api/companies/search")
        data = resp.get_json()
        assert data["items"] == []

    def test_search_by_name_returns_results(self):
        companies = [{"id": "1", "name_th": "ลาซาด้า จำกัด"}]
        with patch("app.pb_list", return_value=companies):
            resp = self.client.get("/api/companies/search?q=ลาซาด้า")
        data = resp.get_json()
        assert data["total"] == 1

    def test_search_by_juristic_id_uses_exact_filter(self):
        """13-digit query must use juristic_id exact match, not LIKE."""
        with patch("app.pb_list", return_value=[]) as mock_pb:
            self.client.get("/api/companies/search?q=0105555040244")
        call_kwargs = mock_pb.call_args[1]
        assert "juristic_id" in call_kwargs.get("filter", "")
        assert "~" not in call_kwargs.get("filter", "")

    def test_search_by_name_uses_partial_match(self):
        with patch("app.pb_list", return_value=[]) as mock_pb:
            self.client.get("/api/companies/search?q=lazada")
        call_kwargs = mock_pb.call_args[1]
        assert "~" in call_kwargs.get("filter", "")

    # ── /api/companies/<id> ──────────────────────────────────────────────────

    def test_get_company_found_returns_200(self):
        company = {"id": "abc123", "name_th": "Test Co"}
        with patch("app.pb_get", return_value=company):
            resp = self.client.get("/api/companies/abc123")
        assert resp.status_code == 200

    def test_get_company_found_returns_company(self):
        company = {"id": "abc123", "name_th": "Test Co"}
        with patch("app.pb_get", return_value=company):
            resp = self.client.get("/api/companies/abc123")
        data = resp.get_json()
        assert data["name_th"] == "Test Co"

    def test_get_company_not_found_returns_404(self):
        with patch("app.pb_get", return_value=None):
            resp = self.client.get("/api/companies/nonexistent")
        assert resp.status_code == 404

    def test_get_company_not_found_error_field(self):
        with patch("app.pb_get", return_value=None):
            resp = self.client.get("/api/companies/nonexistent")
        data = resp.get_json()
        assert "error" in data

    # ── /api/stats ───────────────────────────────────────────────────────────

    def test_stats_returns_200(self):
        with patch("app.pb_list_all", return_value=[]):
            resp = self.client.get("/api/stats")
        assert resp.status_code == 200

    def test_stats_empty_db_returns_total_zero(self):
        with patch("app.pb_list_all", return_value=[]):
            resp = self.client.get("/api/stats")
        data = resp.get_json()
        assert data["total"] == 0

    def test_stats_with_companies_includes_by_size(self):
        companies = [
            {"company_size": "บริษัทขนาดใหญ่", "total_revenue": 100.0, "net_profit": 10.0},
            {"company_size": "บริษัทขนาดเล็ก (SME)", "total_revenue": 5.0, "net_profit": 1.0},
        ]
        with patch("app.pb_list_all", return_value=companies):
            resp = self.client.get("/api/stats")
        data = resp.get_json()
        assert "by_size" in data
        assert data["total"] == 2

    def test_stats_includes_by_province(self):
        companies = [
            {"company_size": "ใหญ่", "province": "กรุงเทพมหานคร",
             "total_revenue": 10.0, "net_profit": 1.0},
        ]
        with patch("app.pb_list_all", return_value=companies):
            resp = self.client.get("/api/stats")
        data = resp.get_json()
        assert "by_province" in data

    def test_stats_avg_roa_computed(self):
        companies = [
            {"roa": 4.92, "total_revenue": 100.0, "net_profit": 10.0},
            {"roa": 3.32, "total_revenue": 50.0, "net_profit": 5.0},
        ]
        with patch("app.pb_list_all", return_value=companies):
            resp = self.client.get("/api/stats")
        data = resp.get_json()
        expected_avg = round((4.92 + 3.32) / 2, 2)
        assert abs(data["avg_roa"] - expected_avg) < 0.001

    def test_stats_total_revenue_sum(self):
        companies = [
            {"total_revenue": 21471.0, "net_profit": 604.6},
            {"total_revenue": 16.9, "net_profit": 1.3},
            {"total_revenue": 737535.0, "net_profit": 8623.0},
        ]
        with patch("app.pb_list_all", return_value=companies):
            resp = self.client.get("/api/stats")
        data = resp.get_json()
        expected = round(21471.0 + 16.9 + 737535.0, 2)
        assert abs(data["total_revenue_sum"] - expected) < 0.01

    def test_stats_roa_none_when_no_data(self):
        companies = [{"total_revenue": 10.0, "net_profit": 1.0}]
        with patch("app.pb_list_all", return_value=companies):
            resp = self.client.get("/api/stats")
        data = resp.get_json()
        # roa not present in mock → safe_avg returns None
        assert data["avg_roa"] is None

    # ── CORS ─────────────────────────────────────────────────────────────────

    def test_cors_header_present(self):
        with patch("app.pb_list", return_value=[]):
            resp = self.client.get("/api/companies", headers={"Origin": "http://localhost:3000"})
        assert "Access-Control-Allow-Origin" in resp.headers

    def test_options_returns_204(self):
        resp = self.client.options("/api/companies")
        assert resp.status_code == 204
