from routers.export import _md_to_html


def test_html_export_escapes_title_and_raw_html():
    output = _md_to_html('<script>alert(1)</script>', '<img src=x onerror=alert(1)>')

    assert '<script>alert(1)</script>' not in output
    assert '<img src=x onerror=alert(1)>' not in output
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in output
    assert '&lt;img src=x onerror=alert(1)&gt;' in output
