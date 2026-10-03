"""The Law 107 write must use migration 030's atomic, metadata-aware RPC."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from fastapi import HTTPException  # noqa: E402
from app.services import transaction_service as tx  # noqa: E402


class _Response:
    def __init__(self, data=None, error=None):
        self.data = data
        self.error = error

    def execute(self):
        if self.error:
            raise self.error
        return self


class _Db:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def rpc(self, name, params):
        self.calls.append((name, params))
        return _Response({"finish_type": "own_last_coin_queen_left"}, self.error)

    def table(self, _name):
        raise AssertionError("official scores must not use sequential fallback")


def _write(db, *, next_set=None, patch=None):
    return tx.apply_board_result(
        db, match_id="match", board_number=4,
        board_patch=patch or {
            "finish_type": "own_last_coin_queen_left",
            "special_finish_extra_point": True,
            "player2_score": 2,
        },
        match_patch={"status": "live"},
        audit={"reason": "Law 107"},
        set_number=1, next_set_number=next_set,
    )


def main():
    db = _Db()
    assert _write(db)["finish_type"] == "own_last_coin_queen_left"
    assert len(db.calls) == 1
    name, params = db.calls[0]
    assert name == "apply_official_board_result"
    assert set(params) == {
        "p_match_id", "p_board_number", "p_board_patch", "p_match_patch",
        "p_audit", "p_next_board_number", "p_set_number", "p_next_set_number",
    }
    assert params["p_next_set_number"] is None

    db = _Db()
    _write(db, next_set=2)
    assert db.calls[0][0] == "apply_official_board_result"
    assert db.calls[0][1]["p_next_set_number"] == 2

    db = _Db(RuntimeError("PGRST202: Could not find the function public.apply_official_board_result"))
    try:
        _write(db)
        raise AssertionError("missing migration 030 was accepted")
    except HTTPException as exc:
        assert exc.status_code == 503 and "030" in exc.detail
    assert len(db.calls) == 1

    db = _Db(RuntimeError("column finish_type does not exist"))
    try:
        _write(db)
        raise AssertionError("missing column was mistaken for the missing RPC")
    except RuntimeError as exc:
        assert "finish_type" in str(exc)
    assert len(db.calls) == 1

    db = _Db()
    _write(db, patch={"player1_score": 3})
    assert db.calls[0][0] == "apply_board_result"
    print("PASS official board writes use migration 030 atomically and fail closed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
