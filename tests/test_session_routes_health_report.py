from __future__ import annotations

import codey.saas.api.session_routes as session_routes


def test_analysis_to_health_report_normalizes_malformed_fields() -> None:
    report = session_routes._analysis_to_health_report(
        {
            "phase": ["Excellent"],
            "health_score": " 0.9 ",
            "coherence": {"value": 0.2},
            "stability": "0.8",
            "total_nodes": " 3 ",
            "total_edges": {"value": 2},
            "summary": {"summary": "Healthy"},
            "recommendations": [" Keep going ", 7, "", None],
        }
    )

    assert report.phase == ""
    assert report.health_score == 0.9
    assert report.coherence == 0.0
    assert report.stability == 0.8
    assert report.total_nodes == 3
    assert report.total_edges == 0
    assert report.summary == ""
    assert report.recommendations == ["Keep going"]


def test_health_report_to_stream_event_uses_normalized_report() -> None:
    report = session_routes._analysis_to_health_report(
        {
            "phase": "Excellent",
            "health_score": "0.9",
            "coherence": "0.2",
            "stability": "0.8",
            "total_nodes": "3",
            "total_edges": "2",
            "summary": "Healthy",
            "recommendations": [" Keep going "],
        }
    )

    event = session_routes._health_report_to_stream_event(report)

    assert event == {
        "type": "health_after",
        "phase": "Excellent",
        "score": 0.9,
        "coherence": 0.2,
        "stability": 0.8,
        "summary": "Healthy",
        "recommendations": ["Keep going"],
    }
