from services.md_render import render_html

# 2026-10-08：HTML 导出的解析/渲染已从 routers/export._md_to_html 收口到 services/md_render，
# 锚点跟着迁到新入口（routers/export 只剩「校验 → 调渲染 → FileResponse」）。


def test_html_export_escapes_title_and_raw_html():
    job = {"title": '<img src=x onerror=alert(1)>'}
    output = render_html(job, '<script>alert(1)</script>')

    assert '<script>alert(1)</script>' not in output
    assert '<img src=x onerror=alert(1)>' not in output
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in output
    assert '&lt;img src=x onerror=alert(1)&gt;' in output