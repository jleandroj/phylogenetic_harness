from harness.report import MANDATORY_SECTIONS, ReportGenerator


def test_report_has_all_mandatory_sections(tmp_path):
    gen = ReportGenerator(tmp_path)
    sections = ReportGenerator.empty_sections()
    sections["1. What was executed"] = ["task t1"]
    paths = gen.generate({
        "run_id": "run_test",
        "summary": "test",
        "scientific_question": "q",
        "sections": sections,
    })
    md = open(paths["markdown"]).read()
    for heading in MANDATORY_SECTIONS:
        assert f"## {heading}" in md, heading


def test_report_json_written(tmp_path):
    gen = ReportGenerator(tmp_path)
    paths = gen.generate({"run_id": "r", "sections": ReportGenerator.empty_sections()})
    import json
    data = json.loads(open(paths["json"]).read())
    assert data["run_id"] == "r"


def test_thirteen_sections():
    assert len(MANDATORY_SECTIONS) == 13
