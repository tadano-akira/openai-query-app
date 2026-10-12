import httpx
import openai
import pytest

from app.services import openai_io


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://finance.yahoo.co.jp/", "finance.yahoo.co.jp"),
        ("https://Finance.Yahoo.co.jp/quote/7203.T?x=1#a", "finance.yahoo.co.jp"),
        ("example.com/path", "example.com"),
        ("http://www.example.com.", "www.example.com"),
    ],
)
def test_normalize_search_domain(url, expected):
    assert openai_io.normalize_search_domain(url) == expected


@pytest.mark.parametrize(
    "url", ["ftp://example.com", "https://localhost/", "http://127.0.0.1", "https://nodot", ""]
)
def test_normalize_search_domain_rejects(url):
    with pytest.raises(openai_io.InvalidSearchUrl):
        openai_io.normalize_search_domain(url)


def test_build_request_search_with_domain():
    req = openai_io.build_request(
        model="m",
        instructions="sys",
        context=[{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}],
        prompt="q",
        web_search_enabled=True,
        searched_domain="example.com",
        max_output_tokens=None,
    )
    assert req["input"][-1] == {"role": "user", "content": "q"}
    assert len(req["input"]) == 3
    assert req["tools"] == [{"type": "web_search", "filters": {"allowed_domains": ["example.com"]}}]
    assert req["tool_choice"] == "required"
    assert "max_output_tokens" not in req


def test_build_request_no_search():
    req = openai_io.build_request(
        model="m",
        instructions="sys",
        context=[],
        prompt="q",
        web_search_enabled=False,
        searched_domain=None,
        max_output_tokens=500,
    )
    assert "tools" not in req and "tool_choice" not in req
    assert req["max_output_tokens"] == 500


def test_parse_response_offsets_citations_and_search():
    resp = {
        "id": "resp_1",
        "status": "completed",
        "output": [
            {
                "type": "web_search_call",
                "id": "ws_1",
                "status": "completed",
                "action": {
                    "type": "search",
                    "query": "q",
                    "sources": [{"url": "https://a.example/"}, {"url": "javascript:alert(1)"}],
                },
            },
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": "Hello ", "annotations": []},
                    {
                        "type": "output_text",
                        "text": "World",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url": "https://a.example/",
                                "title": "A",
                                "start_index": 0,
                                "end_index": 5,
                            },
                            {
                                "type": "url_citation",
                                "url": "javascript:x",
                                "title": "bad",
                                "start_index": 0,
                                "end_index": 1,
                            },
                        ],
                    },
                ],
            },
        ],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
            "input_tokens_details": {"cached_tokens": 2},
            "output_tokens_details": {"reasoning_tokens": 1},
        },
    }
    p = openai_io.parse_response(resp)
    assert p.answer == "Hello World"
    assert p.citations == [{"url": "https://a.example/", "title": "A", "start_index": 6, "end_index": 11}]
    assert p.answer[6:11] == "World"
    assert p.search_info["executed"] is True
    assert p.search_info["calls"][0]["sources"] == [{"url": "https://a.example/"}]
    assert (
        p.usage.input_tokens,
        p.usage.total_tokens,
        p.usage.cached_input_tokens,
        p.usage.reasoning_tokens,
    ) == (10, 15, 2, 1)


def test_parse_response_without_usage_keeps_none():
    p = openai_io.parse_response({"id": "r", "status": "completed", "output": []})
    assert p.usage.total_tokens is None
    assert p.search_info["executed"] is False


def _status_error(cls, status, body):
    req = httpx.Request("POST", "https://api.openai.com/v1/responses")
    return cls("err", response=httpx.Response(status, request=req), body=body)


def test_classify_errors():
    req = httpx.Request("POST", "https://api.openai.com/v1/responses")
    assert openai_io.classify_error(openai.APITimeoutError(request=req)).code == "timeout"
    assert openai_io.classify_error(
        _status_error(openai.RateLimitError, 429, {"code": "rate_limit_exceeded"})
    ).retriable
    assert (
        openai_io.classify_error(
            _status_error(openai.RateLimitError, 429, {"code": "insufficient_quota"})
        ).code
        == "quota_exceeded"
    )
    assert openai_io.classify_error(_status_error(openai.AuthenticationError, 401, None)).code == "auth_error"
    assert (
        openai_io.classify_error(
            _status_error(openai.BadRequestError, 400, {"code": "context_length_exceeded"})
        ).code
        == "context_limit_exceeded"
    )
    e = openai_io.classify_error(_status_error(openai.InternalServerError, 500, None))
    assert (e.code, e.retriable) == ("upstream_error", True)
    assert openai_io.classify_error(ValueError("x")).code == "internal_error"
