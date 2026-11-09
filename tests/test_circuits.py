from datetime import UTC, datetime

from f1data.build.circuits import from_location, from_multiviewer
from f1data.sources import multiviewer, openf1

NOW = datetime(2026, 11, 10, tzinfo=UTC)
MV_URL = "https://api.multiviewer.app/api/v1/circuits/14/2026"


def bbox(points):
    xs, ys = zip(*points, strict=True)
    return min(xs), max(xs), min(ys), max(ys)


def test_multiviewer_interlagos(shared_client):
    c = from_multiviewer("interlagos", "Interlagos", 2026, multiviewer.circuit(shared_client, MV_URL), NOW)
    assert c.source == "official"
    assert len(c.corners) == 15
    assert [k.number for k in c.corners] == list(range(1, 16))
    assert c.rotation == 258
    assert all(isinstance(v, int) for p in c.polyline for v in p)
    assert 4100 < c.length_m < 4400  # official 4309 m; outline is slightly short


def test_multiviewer_outline_is_in_openf1_location_frame(shared_client):
    c = from_multiviewer("interlagos", "Interlagos", 2026, multiviewer.circuit(shared_client, MV_URL), NOW)
    loc = openf1.location(shared_client, 11412, 1, "2026-11-08T18:00:00", "2026-11-08T18:01:30")
    lx0, lx1, ly0, ly1 = bbox([(p["x"], p["y"]) for p in loc])
    mx0, mx1, my0, my1 = bbox(c.polyline)
    # A 90 s window covers most of the lap: the two outlines must coincide within 1%.
    assert abs(lx0 - mx0) < 100 and abs(lx1 - mx1) < 100
    assert abs(ly0 - my0) < 100 and abs(ly1 - my1) < 100


def test_location_fallback(shared_client):
    loc = openf1.location(shared_client, 11412, 1, "2026-11-08T18:00:00", "2026-11-08T18:01:30")
    c = from_location("interlagos", "Interlagos", 2026, loc, NOW)
    assert c.source == "derived"
    assert c.corners == []
    assert 3 <= len(c.polyline) <= 500
    out = c.model_dump(mode="json", by_alias=True)
    assert set(out) >= {"polyline", "corners", "rotation", "lengthM", "schemaVersion", "generatedAt"}
