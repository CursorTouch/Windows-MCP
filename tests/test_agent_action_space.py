from windows_mcp.agent.action_space import decision_from_answers


def test_decision_from_answers_accepts_navigate_choice() -> None:
    probabilities = {
        "DONE": 0.05,
        "WAIT": 0.05,
        "TYPE_TEXT": 0.05,
        "SCROLL_UP": 0.05,
        "CLICK": 0.05,
        "SCROLL_DOWN": 0.05,
        "NAVIGATE_1": 0.65,
        "BLOCKED": 0.05,
    }
    answers = {
        "operation": {
            "choice": "NAVIGATE_1",
            "probabilities": probabilities,
            "confidence": 0.65,
        }
    }
    targets = {
        "CLICK": {"1": {"id": "click:1"}},
        "TYPE_TEXT": {"2": {"id": "type:2"}},
    }

    decision = decision_from_answers(answers, targets, {})

    assert decision["operation"] == "NAVIGATE_1"
    assert decision["choice"] == "NAVIGATE_1"
    assert decision["operation_probabilities"] == probabilities
