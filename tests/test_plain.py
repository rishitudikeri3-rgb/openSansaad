from opensansad.api.plain import check, rewrite


class FakeLLM:
    def __init__(self, answers: list[str]) -> None:
        self.answers, self.users = answers, []

    def json(self, role: str, system: str, user: str) -> dict:
        self.users.append(user)
        return {"plain": self.answers.pop(0)}


def test_check_rejects_new_numbers_speakers_quotes_and_length() -> None:
    claim, quote = "To finish within six months.", "within a period of six months"
    assert check("finish cases within six months", claim, quote) == []
    assert check("finish within 12 months", claim, quote) == ["numbers not in the reason: ['12']"]
    assert check("help, the Government said", claim, quote) == ["names who said it (leave that out)"]
    assert check("to finish faster", claim, quote) == ["starts with 'to'"]
    assert check("x", claim, quote) == ["1 words; use 2 to 20"]


def test_rewrite_reasks_once_then_gives_up() -> None:
    llm = FakeLLM(["finish in 3 months", "finish cases quickly"])
    assert rewrite(llm, "To finish quickly.", "quickly") == "finish cases quickly"
    assert "was rejected: numbers not in the reason: ['3']" in llm.users[1]
    llm = FakeLLM(["in 3 months", "in 4 months"])
    assert rewrite(llm, "To finish quickly.", "quickly") is None
