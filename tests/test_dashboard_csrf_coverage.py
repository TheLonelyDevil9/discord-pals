"""Every dashboard route that changes state rejects a request without a CSRF token.

CSRF gaps came back each time a write route was added without its decorator
(lesson 7 in lessons.md), so this walks the URL map instead of relying on review.
"""

import pytest

import module_stubs  # noqa: F401
import dashboard as dashboard_module

STATE_CHANGING_METHODS = ("POST", "PUT", "PATCH", "DELETE")
PROBE_TOKEN = "csrf-coverage-probe"

# Endpoints that take state-changing requests without a CSRF token by design.
# Give each one the reason, and keep a test of the protection it has instead.
EXEMPT_ENDPOINTS = {
    # GitHub cannot send a dashboard session token. The handler instead requires an
    # HMAC-SHA256 signature over the raw body, which
    # test_project_automation_webhook.py::test_missing_wrong_and_tampered_signatures_are_rejected covers.
    "project_github_webhook",
}


def _state_changing_cases():
    cases = []
    for rule in sorted(dashboard_module.app.url_map.iter_rules(), key=lambda r: r.rule):
        if rule.endpoint in EXEMPT_ENDPOINTS:
            continue
        for method in STATE_CHANGING_METHODS:
            if method in rule.methods:
                cases.append(pytest.param(rule, method, id=f"{method} {rule.rule}"))
    return cases


CASES = _state_changing_cases()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASS", "csrf-coverage")
    dashboard_module.app.config["TESTING"] = True
    test_client = dashboard_module.app.test_client()
    with test_client.session_transaction() as session:
        session["logged_in"] = True
        session["csrf_token"] = PROBE_TOKEN
    return test_client


class _ViewBodyStub:
    """Takes the place of a route's own function so that an unprotected route
    cannot run its real handler, such as an update or a restart, during the test."""

    def __init__(self):
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return "view body reached", 200


def _replace_view_body(endpoint, stub):
    """Point the innermost decorator at ``stub`` and return a function that undoes it.

    Decorators built with functools.wraps expose the function they wrap as
    __wrapped__ and call it through a closure cell, so swapping that cell keeps
    every decorator, the CSRF check included, in the request path.
    """
    wrapper, body = None, dashboard_module.app.view_functions[endpoint]
    while getattr(body, "__wrapped__", None) is not None:
        wrapper, body = body, body.__wrapped__
    if wrapper is None:
        pytest.fail(f"{endpoint} has no decorator, so nothing checks a CSRF token before its body runs")

    def holds_body(cell):
        try:
            return cell.cell_contents is body
        except ValueError:
            return False

    cells = [cell for cell in wrapper.__closure__ or () if holds_body(cell)]
    if len(cells) != 1:
        pytest.fail(f"cannot separate the body of {endpoint} from its decorators")
    cells[0].cell_contents = stub

    def restore():
        cells[0].cell_contents = body

    return restore


def _send(client, rule, method, token):
    values = {argument: "1" for argument in rule.arguments}
    url = dashboard_module.app.url_map.bind("localhost").build(rule.endpoint, values, method=method)
    headers = {"X-CSRF-Token": token} if token else {}
    return client.open(url, method=method, headers=headers)


def test_the_url_map_has_state_changing_routes_to_check():
    assert CASES, "the URL map walk found no state-changing routes, so the CSRF test checks nothing"


def test_every_exempt_endpoint_is_still_registered():
    registered = {rule.endpoint for rule in dashboard_module.app.url_map.iter_rules()}

    assert EXEMPT_ENDPOINTS <= registered


@pytest.mark.parametrize(("rule", "method"), CASES)
def test_state_changing_route_rejects_a_request_without_a_csrf_token(client, rule, method):
    stub = _ViewBodyStub()
    restore = _replace_view_body(rule.endpoint, stub)
    try:
        rejected = _send(client, rule, method, token=None)
        assert (rejected.status_code, stub.calls) == (403, 0), f"{method} {rule.rule} runs without a CSRF token"

        _send(client, rule, method, token=PROBE_TOKEN)
        assert stub.calls == 1, f"{method} {rule.rule} did not reach its handler with a valid token"
    finally:
        restore()
