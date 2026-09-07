from eval.adapters.math_grade import answers_equal, extract_boxed, grade_math


def test_extract_last_boxed():
    assert extract_boxed(r"draft \boxed{1} final \boxed{42}") == "42"


def test_grade_aime_integer():
    ok, pred = grade_math(r"The answer is \boxed{003}", "3")
    assert ok
    assert pred == "003"


def test_answers_equal_plain():
    assert answers_equal("4", "4")
    assert not answers_equal("4", "5")
