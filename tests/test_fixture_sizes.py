from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024


def test_no_fixture_exceeds_5_mb():
    root = Path(__file__).parent / "fixtures"
    too_big = [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and p.stat().st_size > MAX_BYTES]
    assert not too_big, f"fixtures over 5 MB: {too_big}"
