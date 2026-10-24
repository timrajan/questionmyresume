from app.llm import normalize_judgement


def test_normalize_clamps_and_fills_defaults():
    out = normalize_judgement({
        "overall_match": "140",
        "resume_quality": None,
        "category_scores": [{"name": "Skills", "score": 55.6}, "junk"],
        "rejection_reasons": [{"title": f"r{i}"} for i in range(5)],
        "missing_keywords": ["SQL", "", None],
    })
    assert out["overall_match"] == 100
    assert out["resume_quality"] == 0
    assert out["category_scores"] == [{"name": "Skills", "score": 56, "comment": ""}]
    assert len(out["rejection_reasons"]) == 3
    assert out["rejection_reasons"][0]["fix"] == ""
    assert out["missing_keywords"] == ["SQL"]
    assert out["rewritten_bullets"] == []
