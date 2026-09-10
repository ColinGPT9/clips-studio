"""Why a run produced no clips.

The risk this guards is not "it fails to explain". It is "it explains
confidently and wrongly". Telling someone their talking-head video had nobody
on screen is worse than saying nothing, so most of these are about the cause
NOT being named when the evidence is thin.
"""

from core.outcome import MIN_EVIDENCE, explain_no_clips, summarise_run


class Candidate:
    def __init__(self, score, **subscores):
        self.score = score
        self.subscores = subscores


class Rejection:
    def __init__(self, candidate, reason):
        self.candidate = candidate
        self.reason = reason


CONFIG = {"clips": {"min_score": 55}}


def measured(reaction, score=40):
    """A candidate the detector actually looked at."""
    return Candidate(score, reaction=reaction, reaction_measured=1)


def unmeasured(score=40):
    """A candidate carrying the neutral placeholder — never looked at."""
    return Candidate(score, reaction=50)


def rejected(cands, reason="below_min_score"):
    return [Rejection(c, reason) for c in cands]


# ---- the numbers -----------------------------------------------------------


def test_a_run_with_clips_names_no_cause():
    out = summarise_run([Candidate(80)], rejected([Candidate(20)]), CONFIG)
    assert out["clips"] == 1
    assert out["cause"] is None, "there is nothing to explain when clips came out"


def test_counts_and_best_score_span_kept_and_rejected():
    out = summarise_run([Candidate(70)], rejected([Candidate(30), Candidate(51)]), CONFIG)
    assert out["candidates"] == 3
    assert out["best_score"] == 70
    assert out["min_score"] == 55


def test_best_score_comes_from_rejections_when_nothing_was_kept():
    out = summarise_run([], rejected([Candidate(31), Candidate(47), Candidate(12)]), CONFIG)
    assert out["clips"] == 0
    assert out["best_score"] == 47, "the near miss is the useful number to show"


def test_rejection_reasons_are_counted():
    out = summarise_run(
        [], rejected([Candidate(10)]) + rejected([Candidate(90)], "overlap"), CONFIG
    )
    assert out["rejected"] == {"below_min_score": 1, "overlap": 1}


def test_an_empty_run_is_not_an_error():
    out = summarise_run([], [], CONFIG)
    assert out["candidates"] == 0
    assert out["best_score"] is None
    assert out["cause"] == "no_candidates"


# ---- naming "nobody on screen" ---------------------------------------------


def test_gameplay_is_named_when_the_detector_looked_and_found_nobody():
    out = summarise_run([], rejected([measured(0) for _ in range(10)]), CONFIG)
    assert out["cause"] == "no_people"
    assert out["measured"] == 10
    assert out["nothing_detected"] == 10


def test_one_stray_detection_does_not_veto_an_otherwise_clear_read():
    """A face in a stream overlay should not silence the explanation."""
    cands = [measured(0) for _ in range(9)] + [measured(60)]
    assert summarise_run([], rejected(cands), CONFIG)["cause"] == "no_people"


def test_a_quiet_talking_head_video_is_NOT_called_gameplay():
    """The false positive that would make the app confidently wrong: people
    ARE on screen, the clips just were not good enough."""
    cands = [measured(70, score=48) for _ in range(10)]
    out = summarise_run([], rejected(cands), CONFIG)
    assert out["cause"] == "below_threshold"
    assert out["cause"] != "no_people"


def test_placeholder_reactions_are_never_treated_as_evidence():
    """Reaction defaults to 50 for candidates that never got the expensive
    pass. Reading that as 'no people' would be claiming something about
    windows nobody looked at."""
    out = summarise_run([], rejected([unmeasured() for _ in range(20)]), CONFIG)
    assert out["measured"] == 0
    assert out["cause"] != "no_people"


def test_too_little_evidence_to_generalise_stays_quiet():
    cands = [measured(0) for _ in range(MIN_EVIDENCE - 1)]
    assert summarise_run([], rejected(cands), CONFIG)["cause"] != "no_people"


def test_exactly_the_evidence_threshold_is_enough():
    cands = [measured(0) for _ in range(MIN_EVIDENCE)]
    assert summarise_run([], rejected(cands), CONFIG)["cause"] == "no_people"


def test_a_mixed_read_does_not_claim_gameplay():
    cands = [measured(0) for _ in range(5)] + [measured(70) for _ in range(5)]
    assert summarise_run([], rejected(cands), CONFIG)["cause"] != "no_people"


def test_duplicates_are_named_when_they_dominate():
    out = summarise_run(
        [],
        rejected([Candidate(30)]) + rejected([Candidate(90) for _ in range(6)], "overlap"),
        CONFIG,
    )
    assert out["cause"] == "duplicates"


# ---- the log line ----------------------------------------------------------


def test_the_log_line_carries_the_numbers():
    out = summarise_run([], rejected([Candidate(47)]), CONFIG)
    line = explain_no_clips(out)
    assert "47" in line and "55" in line


def test_the_gameplay_line_says_what_was_measured():
    out = summarise_run([], rejected([measured(0) for _ in range(6)]), CONFIG)
    line = explain_no_clips(out)
    assert "6 of 6" in line
    assert "gameplay" in line


def test_the_generic_line_points_at_the_setting_that_would_help():
    out = summarise_run([], rejected([Candidate(50) for _ in range(4)]), CONFIG)
    assert "min_score" in explain_no_clips(out)


def test_explaining_an_empty_summary_does_not_raise():
    assert explain_no_clips({})


# ---- what actually reaches a bug report ------------------------------------


def test_the_video_question_is_no_longer_dropped_from_the_report():
    """It was required, answered, and then not rendered — so the single most
    useful field on a bug report never arrived."""
    from server.feedback import build_markdown

    md = build_markdown(
        "bug",
        {"source": "Big Buck Bunny (abc123)", "trying": "get clips",
         "happened": "none came out", "expected": "clips"},
        None,
    )
    assert "Which video were you processing?" in md
    assert "Big Buck Bunny (abc123)" in md


def test_every_required_bug_field_has_somewhere_to_appear():
    """The mismatch that caused this: REQUIRED_FIELDS demanded an answer that
    build_markdown had no case for."""
    from server.feedback import REQUIRED_FIELDS, build_markdown

    answers = {key: f"answer-for-{key}" for key, _ in REQUIRED_FIELDS["bug"]}
    md = build_markdown("bug", answers, None)
    for key, label in REQUIRED_FIELDS["bug"]:
        assert f"answer-for-{key}" in md, f"{label!r} is required but never rendered"


def test_the_run_summary_round_trips_through_the_database(db):
    db.conn.execute(
        "INSERT INTO videos (video_id, title, status, created_at, updated_at) "
        "VALUES ('v1','t','done','2026-01-01','2026-01-01')"
    )
    db.conn.commit()
    summary = summarise_run([], rejected([measured(0) for _ in range(5)]), CONFIG)
    db.set_outcome("v1", summary)

    back = db.get_outcome("v1")
    assert back["cause"] == "no_people"
    assert back["candidates"] == 5


def test_a_video_with_no_recorded_outcome_reads_as_empty(db):
    db.conn.execute(
        "INSERT INTO videos (video_id, title, status, created_at, updated_at) "
        "VALUES ('v2','t','done','2026-01-01','2026-01-01')"
    )
    db.conn.commit()
    assert db.get_outcome("v2") == {}
    assert db.get_outcome("never-existed") == {}


def test_rejections_keep_the_score_breakdown(db):
    db.conn.execute(
        "INSERT INTO videos (video_id, title, status, created_at, updated_at) "
        "VALUES ('v3','t','done','2026-01-01','2026-01-01')"
    )
    db.conn.commit()
    db.log_rejection("v3", 1.0, 5.0, 40, "below_min_score",
                     subscores={"reaction": 0, "reaction_measured": 1})
    row = db.conn.execute("SELECT subscores FROM rejections WHERE video_id='v3'").fetchone()
    assert '"reaction_measured": 1' in row["subscores"]


def test_a_rejection_without_subscores_still_writes(db):
    db.conn.execute(
        "INSERT INTO videos (video_id, title, status, created_at, updated_at) "
        "VALUES ('v4','t','done','2026-01-01','2026-01-01')"
    )
    db.conn.commit()
    db.log_rejection("v4", 1.0, 5.0, 40, "overlap")
    assert db.conn.execute("SELECT COUNT(*) c FROM rejections WHERE video_id='v4'").fetchone()["c"] == 1
