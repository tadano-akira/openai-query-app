import pytest

from app.services import openai_io, prompts

# モード導入前のシステム指示。標準モードはこれと完全に一致させる（ふるまいを変えない）
LEGACY_SYSTEM_PROMPT = (
    "あなたは日本語で回答するアシスタントです。"
    "Web検索結果を使う場合は出典を明示し、取得できなかった情報は推測で補わずその旨を述べてください。"
)


def test_standard_mode_is_identical_to_legacy_prompt():
    instr = prompts.build_instructions(None)
    assert instr.mode.id == "standard"
    assert instr.text == LEGACY_SYSTEM_PROMPT
    assert instr.mode.verbosity is None and instr.mode.reasoning_effort is None


def test_bundled_modes_and_token_saver_params():
    modes = {m.id: m for m in prompts.load_modes()}
    assert list(modes) == ["standard", "token-saver", "business", "easy"]
    ts = prompts.build_instructions("token-saver")
    assert ts.text.startswith(LEGACY_SYSTEM_PROMPT + "\n\n## 回答スタイル: トークン節約")
    assert (ts.mode.verbosity, ts.mode.reasoning_effort) == ("low", "low")
    # 版はモードごとに異なる
    assert len({prompts.build_instructions(m).version for m in modes}) == len(modes)


def test_unknown_mode():
    with pytest.raises(prompts.UnknownMode):
        prompts.build_instructions("nope")


def test_parse_mode_strips_comments_and_validates(tmp_path, monkeypatch):
    (tmp_path / "modes").mkdir()
    (tmp_path / "base.md").write_text("<!-- メモ -->\n基本", encoding="utf-8")
    (tmp_path / "modes" / "standard.md").write_text("---\nlabel: 標準\n---\n", encoding="utf-8")
    (tmp_path / "modes" / "x.md").write_text(
        "<!-- 説明 -->\n---\n# コメント行\nlabel: X\norder: 5\nverbosity: low  # 補足\n---\n本文<!-- 内部メモ -->\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(prompts, "PROMPTS_DIR", tmp_path)
    instr = prompts.build_instructions("x")
    assert instr.text == "基本\n\n本文"
    assert instr.mode.verbosity == "low" and instr.mode.order == 5

    (tmp_path / "modes" / "bad.md").write_text("---\nlabel: B\nverbosity: tiny\n---\n", encoding="utf-8")
    with pytest.raises(prompts.PromptError, match="verbosity"):
        prompts.load_modes()


@pytest.mark.parametrize(
    ("model", "expected"),
    [("gpt-5.6-luna", True), ("gpt-5.5", True), ("gpt-4.1", False), ("gpt-4.1-mini", False)],
)
def test_mode_params_only_for_supported_models(model, expected):
    req = openai_io.build_request(
        model=model,
        instructions="i",
        context=[],
        prompt="p",
        web_search_enabled=False,
        searched_domain=None,
        max_output_tokens=None,
        verbosity="low",
        reasoning_effort="low",
    )
    assert ("text" in req and "reasoning" in req) is expected
    if expected:
        assert req["text"] == {"verbosity": "low"} and req["reasoning"] == {"effort": "low"}
