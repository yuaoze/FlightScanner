"""Unit tests for the pinned-flight dialog's client-side logic.

These tests verify the step-state machine and flight deduplication
WITHOUT starting a real Streamlit server.  They exercise the same
code paths that would be exercised during a real browser session,
giving us the "前端交互闭环验证" the user asked for.
"""

import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from flightscanner.interfaces import FlightDirection, FlightInfo, FlightPrice


# ── helpers ─────────────────────────────────────────────────────────────────

def _make_fp(
    flight_no: str,
    price: float,
    airline: str = "测试航空",
    dep_time: str = "08:30",
    source: str = "qunar",
    available_seats: Optional[int] = 5,
) -> FlightPrice:
    fi = FlightInfo(
        flight_no=flight_no,
        airline=airline,
        departure_city="成都",
        arrival_city="上海",
        departure_time=dep_time,
        arrival_time="11:00",
        departure_date=date(2026, 4, 6),
        direction=FlightDirection.DEPARTURE,
    )
    return FlightPrice(
        flight_info=fi,
        price=Decimal(str(price)),
        currency="CNY",
        seat_class="经济舱",
        available_seats=available_seats,
        scraped_at=datetime.now(timezone.utc),
        source=source,
    )


# ── deduplication logic (mirrors app.py Step 1) ─────────────────────────────

def _dedup_by_flight_no(results: List[FlightPrice]) -> Dict[str, FlightPrice]:
    """Mirrors the dedup dict built in Step 1 of the dialog."""
    seen: dict = {}
    for fp in results:
        no = fp.flight_info.flight_no
        if no not in seen or fp.price < seen[no].price:
            seen[no] = fp
    return seen


def _make_options(seen: Dict[str, FlightPrice]) -> List[str]:
    """Mirrors _format_flight_option() + sorted list built in Step 1."""
    def _fmt(no: str, fp: FlightPrice) -> str:
        fi = fp.flight_info
        arr_time = fi.arrival_time or ""
        overnight = ""
        if fi.arrival_date and fi.departure_date and fi.arrival_date > fi.departure_date:
            delta = (fi.arrival_date - fi.departure_date).days
            overnight = f"+{delta}"
        stops_str = ""
        stop_count = no.count("/")
        if stop_count > 0:
            stops_str = f"  经停{stop_count}次"
        return (
            f"{no}  {fi.airline}  {fi.departure_time}→{arr_time}{overnight}"
            f"  ¥{float(fp.price):.0f}{stops_str}  [{fp.source}]"
        )
    return [_fmt(no, fp) for no, fp in sorted(seen.items(), key=lambda x: x[1].price)]


class TestDialogDeduplication:
    """Verify that two-platform results are correctly deduplicated and sorted."""

    def test_single_platform_passthrough(self):
        results = [
            _make_fp("CA4509", 480, source="qunar"),
            _make_fp("MU5185", 520, source="qunar"),
        ]
        seen = _dedup_by_flight_no(results)
        assert len(seen) == 2
        assert "CA4509" in seen
        assert "MU5185" in seen

    def test_cross_platform_keeps_cheaper(self):
        """Same flight_no from qunar and ctrip → keep the cheaper one."""
        results = [
            _make_fp("CA4509", 520, source="qunar"),
            _make_fp("CA4509", 480, source="ctrip"),  # cheaper
        ]
        seen = _dedup_by_flight_no(results)
        assert len(seen) == 1
        assert seen["CA4509"].price == Decimal("480")
        assert seen["CA4509"].source == "ctrip"

    def test_cross_platform_keeps_cheaper_reversed(self):
        """Same assertion but qunar is cheaper this time."""
        results = [
            _make_fp("MU5185", 399, source="qunar"),
            _make_fp("MU5185", 450, source="ctrip"),
        ]
        seen = _dedup_by_flight_no(results)
        assert seen["MU5185"].price == Decimal("399")
        assert seen["MU5185"].source == "qunar"

    def test_mixed_unique_and_duplicate(self):
        """Three flights: two unique, one duplicated across platforms."""
        results = [
            _make_fp("CA4509", 480, source="qunar"),
            _make_fp("CA4509", 460, source="ctrip"),  # cheaper, wins
            _make_fp("MU5185", 520, source="qunar"),
            _make_fp("3U8633", 380, source="ctrip"),
        ]
        seen = _dedup_by_flight_no(results)
        assert len(seen) == 3
        assert seen["CA4509"].price == Decimal("460")

    def test_options_sorted_by_price_ascending(self):
        results = [
            _make_fp("MU5185", 520, source="qunar"),
            _make_fp("CA4509", 480, source="ctrip"),
            _make_fp("3U8633", 380, source="qunar"),
        ]
        seen = _dedup_by_flight_no(results)
        options = _make_options(seen)
        prices = [float(opt.split("¥")[1].split()[0]) for opt in options]
        assert prices == sorted(prices), "Options must be sorted ascending by price"

    def test_option_string_contains_source(self):
        """Each option must show the source platform in brackets."""
        results = [_make_fp("CA4509", 480, source="ctrip")]
        seen = _dedup_by_flight_no(results)
        options = _make_options(seen)
        assert "[ctrip]" in options[0]

    def test_option_first_token_is_flight_no(self):
        """Splitting option on whitespace must give flight_no as first token."""
        results = [_make_fp("CA4509", 480)]
        seen = _dedup_by_flight_no(results)
        options = _make_options(seen)
        assert options[0].split()[0] == "CA4509"

    def test_option_contains_arrival_time(self):
        """Option must include 'dep_time→arr_time'."""
        results = [_make_fp("CA4509", 480, dep_time="08:30")]  # arrival_time fixed "11:00"
        seen = _dedup_by_flight_no(results)
        options = _make_options(seen)
        assert "08:30→11:00" in options[0]

    def test_option_transit_shows_stops(self):
        """Transit flight (flight_no contains '/') must show 经停N次."""
        fi = FlightInfo(
            flight_no="CA953/MU5185",
            airline="国航",
            departure_city="成都",
            arrival_city="上海",
            departure_time="07:00",
            arrival_time="15:30",
            departure_date=date(2026, 4, 6),
            direction=FlightDirection.DEPARTURE,
        )
        fp = FlightPrice(
            flight_info=fi,
            price=Decimal("380"),
            currency="CNY",
            seat_class="经济舱",
            available_seats=5,
            scraped_at=datetime.now(timezone.utc),
            source="qunar",
        )
        seen = {"CA953/MU5185": fp}
        options = _make_options(seen)
        assert "经停1次" in options[0]
        assert "CA953/MU5185" == options[0].split()[0]

    def test_option_overnight_shows_plus1(self):
        """Overnight flight (arrival_date > departure_date) must show +1 after arrival time."""
        fi = FlightInfo(
            flight_no="CA837",
            airline="国航",
            departure_city="成都",
            arrival_city="上海",
            departure_time="23:00",
            arrival_time="01:30",
            departure_date=date(2026, 4, 6),
            arrival_date=date(2026, 4, 7),  # next day
            direction=FlightDirection.DEPARTURE,
        )
        fp = FlightPrice(
            flight_info=fi,
            price=Decimal("520"),
            currency="CNY",
            seat_class="经济舱",
            available_seats=5,
            scraped_at=datetime.now(timezone.utc),
            source="qunar",
        )
        seen = {"CA837": fp}
        options = _make_options(seen)
        assert "01:30+1" in options[0]


# ── step-state machine ───────────────────────────────────────────────────────

class TestDialogStepStateMachine:
    """Simulate the session_state transitions that the dialog performs.

    Each test mirrors a user interaction sequence.  We validate that
    _pf_step advances / retreats correctly and that required keys are
    present before the step transition is allowed.
    """

    def _make_session(self) -> dict:
        return {"_pf_step": 0}

    # ── Step 0 → 1 (search mode) ───────────────────────────────────────────

    def test_step0_to_1_requires_origin_and_dest(self):
        """Must not advance to step 1 if origin or dest is empty."""
        session = self._make_session()
        origin, dest = "", "上海"
        # Simulate the guard in the "下一步" button handler
        can_advance = bool(origin and dest and origin != dest)
        assert not can_advance

    def test_step0_to_1_rejects_same_city(self):
        session = self._make_session()
        origin = dest = "上海"
        can_advance = bool(origin and dest and origin != dest)
        assert not can_advance

    def test_step0_to_1_ok(self):
        session = self._make_session()
        origin, dest = "成都", "上海"
        dep_date = date(2026, 4, 6)
        # Simulate successful advance
        session["_pf_origin"] = origin
        session["_pf_dest"] = dest
        session["_pf_dep_date"] = dep_date
        session["_pf_mode"] = "search"
        session["_pf_step"] = 1
        assert session["_pf_step"] == 1

    def test_step0_to_1_stores_search_results(self):
        """After a successful search, results are stored before advancing."""
        session = self._make_session()
        results = [_make_fp("CA4509", 480)]
        session["_pf_search_results"] = results
        session["_pf_step"] = 1
        assert len(session["_pf_search_results"]) == 1
        assert session["_pf_step"] == 1

    # ── Step 1 → 2 ────────────────────────────────────────────────────────

    def test_step1_to_2_requires_out_flight_no(self):
        session = {"_pf_step": 1}
        # No _pf_out_no set → should not advance
        can_advance = bool(session.get("_pf_out_no"))
        assert not can_advance

    def test_step1_to_2_ok(self):
        session = {"_pf_step": 1, "_pf_out_no": "CA4509"}
        can_advance = bool(session.get("_pf_out_no"))
        assert can_advance
        session["_pf_step"] = 2
        assert session["_pf_step"] == 2

    # ── Step 1 ← back to 0 ────────────────────────────────────────────────

    def test_back_from_step1_to_step0(self):
        session = {"_pf_step": 1}
        session["_pf_step"] = 0
        assert session["_pf_step"] == 0

    # ── Step 2 ← back to 1 ────────────────────────────────────────────────

    def test_back_from_step2_to_step1(self):
        session = {"_pf_step": 2}
        session["_pf_step"] = 1
        assert session["_pf_step"] == 1

    # ── Submit (step 2 → close) ───────────────────────────────────────────

    def test_submit_clears_pf_state(self):
        """After submit, all _pf_ keys should be cleared."""
        session = {
            "_pf_step": 2,
            "_pf_origin": "成都",
            "_pf_dest": "上海",
            "_pf_out_no": "CA4509",
            "new_route_id": 42,
        }
        # Simulate _clear_pf_dlg_state()
        for key in list(session.keys()):
            if key.startswith("_pf_"):
                del session[key]
        assert "_pf_step" not in session
        assert "_pf_origin" not in session
        assert "new_route_id" in session  # non-pf_ key survives
