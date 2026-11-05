import json
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
NODE_FIXTURE = ROOT / "tests" / "web_output_security_node.cjs"
APP_JS = ROOT / "src" / "service" / "static" / "app.js"
MARKED_JS = ROOT / "src" / "service" / "static" / "vendor" / "marked.umd.js"


def render(markdown="", use_marked=True, action=None, response=None):
    payload = {
        "markdown": markdown,
        "useMarked": use_marked,
        "appPath": str(APP_JS),
        "markedPath": str(MARKED_JS),
    }
    if action:
        payload.update({"action": action, "response": response})
    result = subprocess.run(
        ["node", str(NODE_FIXTURE)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


class ReportParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.hrefs = []
        self.text = []
        self.event_attrs = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        for name, value in attrs:
            if name.lower().startswith("on"):
                self.event_attrs.append((name.lower(), value or ""))
            if name.lower() == "href":
                self.hrefs.append(value or "")

    def handle_data(self, data):
        self.text.append(data)


def assert_no_active_payload(parser):
    assert not ({"script", "img", "svg", "iframe", "object"} & set(parser.tags))
    assert not any(name in {"onerror", "onload", "onmouseover"} for name, _ in parser.event_attrs)
    assert all(urlsplit(href).scheme in {"", "http", "https", "mailto"} for href in parser.hrefs)


def test_untrusted_html_and_urls_are_inert():
    markdown = """# Report

<img src=x onerror=alert(1)>
<svg onload=alert(2)><script>alert(3)</script></svg>
<a href="javascript:alert(4)" onclick="alert(5)">raw link</a>

[bad-js](javascript:alert(6)) [bad-data](data:text/html,boom)
[bad-encoded](javascript&#x3a;alert(7)) [good](https://example.com/safe)
![remote image](https://example.com/tracker.png)
![bad image](data:text/html,boom) [quoted](https://example.com/ "bad\" onclick=\"alert(8)")
"""
    result = render(markdown)
    parser = ReportParser()
    parser.feed(result["html"])
    parser.close()

    assert_no_active_payload(parser)
    assert "https://example.com/safe" in parser.hrefs
    assert "https://example.com/" in parser.hrefs
    assert "raw link" in "".join(parser.text)
    assert "bad-js" in "".join(parser.text)
    assert "bad-data" in "".join(parser.text)


def test_normal_markdown_stays_formatted_and_code_is_escaped():
    markdown = """# Heading

First line
second line

- item

```html
<img src=x onerror=alert(1)>
```
"""
    result = render(markdown)
    parser = ReportParser()
    parser.feed(result["html"])
    parser.close()

    assert {"h1", "p", "br", "ul", "li", "pre", "code"}.issubset(parser.tags)
    assert_no_active_payload(parser)
    assert "Heading" in "".join(parser.text)
    assert "First line" in "".join(parser.text) and "second line" in "".join(parser.text)
    assert "<img src=x onerror=alert(1)>" in "".join(parser.text)
    assert "img" not in parser.tags


def test_missing_marked_uses_plain_text():
    markdown = "# Report\n<img src=x onerror=alert(1)>"
    result = render(markdown, use_marked=False)
    assert result["html"] == ""
    assert result["text"] == markdown


def test_history_api_fields_are_encoded_or_normalized():
    result = render(action="history", response=[{
        "pr_number": "42",
        "title": '<img src=x onerror="alert(1)">',
        "analyzed_at": '<svg onload="alert(2)">',
        "risk_level": '"><img src=x onerror="alert(3)">',
        "files_count": '2<img src=x onerror="alert(4)">',
        "additions": "12",
        "deletions": "3",
        "suggestions_count": '4<img src=x onerror="alert(5)">',
        "used_ai": False,
        "html_url": "javascript:alert(6)",
    }])
    parser = ReportParser()
    parser.feed(result["html"] + result["summary"])
    parser.close()
    assert_no_active_payload(parser)
    assert 'href="#"' in result["html"]
    assert "+12/-3" in result["html"]
    assert "0 suggestions" in result["html"]
    assert "1" in result["summary"]

    normal = render(action="history", response=[{
        "pr_number": 42, "title": "Safe title", "analyzed_at": "20261026123456",
        "risk_level": "high", "files_count": 5, "additions": 10,
        "deletions": 2, "suggestions_count": 3, "used_ai": True,
        "html_url": "https://github.com/acme/repo/pull/42",
    }])
    assert "#42" in normal["html"] and "5 files" in normal["html"]
    assert "+10/-2" in normal["html"] and "3 suggestions" in normal["html"]


def test_timeline_api_fields_are_encoded_or_normalized():
    result = render(action="timeline", response=[{
        "pr_number": '7<img src=x onerror="alert(1)">',
        "title": '<svg onload="alert(2)">title',
        "risk_level": '"><script>alert(3)</script>',
        "html_url": "data:text/html,boom",
        "files_count": '1<img src=x onerror="alert(4)">',
        "additions": "8", "deletions": "2",
        "findings_count": '3<img src=x onerror="alert(5)">',
        "top_findings": [{"severity": '"><svg onload="alert(6)">', "title": '<img src=x onerror="alert(7)">'}],
    }])
    parser = ReportParser()
    parser.feed(result["html"] + result["summary"])
    parser.close()
    assert_no_active_payload(parser)
    assert 'href="#"' in result["html"]
    assert "+8/-2" in result["html"]
    assert "0 findings" in result["html"]
    assert "0" in result["summary"]

    normal = render(action="timeline", response=[{
        "pr_number": 7, "title": "Safe PR", "risk_level": "medium",
        "html_url": "https://github.com/acme/repo/pull/7", "files_count": 4,
        "additions": 20, "deletions": 5, "findings_count": 2,
        "top_findings": [{"severity": "high", "title": "Safe finding"}],
    }])
    assert "#7" in normal["html"] and "4 files" in normal["html"]
    assert "+20/-5" in normal["html"] and "2 findings" in normal["html"]
    assert 'href="https://github.com/acme/repo/pull/7"' in normal["html"]
