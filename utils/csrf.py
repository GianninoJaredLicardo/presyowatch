"""
CSRF protection for every POST form, without editing each template.

- Each session gets a random token.
- On the way out, every HTML page has a hidden csrf_token field added to each
  <form method="post">.
- On the way in, any POST/PUT/PATCH/DELETE without the matching token is refused,
  so another website can't make a logged-in user's browser submit actions here.

Templates can also call {{ csrf_token() }} directly; JavaScript can send the
token in an X-CSRF-Token header (it is in <meta name="csrf-token">).
"""
import hmac
import re
import secrets

from flask import flash, redirect, request, session
from markupsafe import escape

_FIELD = "csrf_token"
_UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
# A whole <form ...> tag, allowing ">" inside quoted attributes (e.g. onsubmit="return confirm('a > b')").
_FORM_TAG_RE = re.compile(r"""<form\b(?:[^>"']|"[^"]*"|'[^']*')*>""", re.I)
_METHOD_POST_RE = re.compile(r"""\bmethod\s*=\s*["']?post\b""", re.I)
_HEAD_RE = re.compile(r"</head>", re.I)


def csrf_token():
    token = session.get("_csrf")
    if not token:
        token = session["_csrf"] = secrets.token_urlsafe(32)
    return token


def init_csrf(app):
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def _check_csrf():
        if request.method not in _UNSAFE or app.config.get("CSRF_DISABLED"):
            return None
        sent = request.form.get(_FIELD) or request.headers.get("X-CSRF-Token") or ""
        expected = session.get("_csrf") or ""
        if expected and hmac.compare_digest(sent, expected):
            return None
        # Most common real cause: the page was open so long the session expired.
        flash("Your session expired or the form was out of date. Please try again.", "warning")
        return redirect(request.referrer or "/", code=303)

    @app.after_request
    def _inject_csrf(response):
        if response.mimetype != "text/html" or response.direct_passthrough or response.status_code >= 300:
            return response
        html = response.get_data(as_text=True)
        if "<form" not in html.lower() and "</head>" not in html.lower():
            return response
        token = escape(csrf_token())
        hidden = f'<input type="hidden" name="{_FIELD}" value="{token}">'
        html = _FORM_TAG_RE.sub(lambda m: m.group(0) + hidden if _METHOD_POST_RE.search(m.group(0)) else m.group(0), html)
        html = _HEAD_RE.sub(f'<meta name="csrf-token" content="{token}">\n</head>', html, count=1)
        response.set_data(html)
        return response
