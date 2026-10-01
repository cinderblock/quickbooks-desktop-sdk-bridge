"""A session is only ever used on the configured company file."""

import pytest

from qb_bridge.qb.company import same_company_file
from qb_bridge.qb.exceptions import QBSessionError

CONFIGURED = r"C:\Books\Acme Widgets.QBW"
OTHER = r"C:\Books\Some Other Company.QBW"


class TestSameCompanyFile:
    def test_identical_paths_match(self):
        assert same_company_file(CONFIGURED, CONFIGURED)

    def test_case_and_slash_direction_do_not_matter(self):
        assert same_company_file("c:/books/ACME WIDGETS.qbw", CONFIGURED)

    def test_dot_segments_and_quotes_do_not_matter(self):
        assert same_company_file(r'"C:\Books\.\sub\..\Acme Widgets.QBW"', CONFIGURED)

    def test_a_different_file_does_not_match(self):
        assert not same_company_file(OTHER, CONFIGURED)

    def test_the_same_name_in_another_folder_does_not_match(self):
        assert not same_company_file(r"D:\Old\Acme Widgets.QBW", CONFIGURED)

    def test_a_share_path_is_not_assumed_to_be_the_local_file(self):
        assert not same_company_file(r"\\server\Books\Acme Widgets.QBW", CONFIGURED)

    @pytest.mark.parametrize(("actual", "expected"), [("", CONFIGURED), (CONFIGURED, ""), ("", "")])
    def test_an_unknown_file_never_matches(self, actual, expected):
        assert not same_company_file(actual, expected)


class FakeRequestProcessor:
    """Stands in for QBXMLRP2.RequestProcessor."""

    def __init__(self, open_file, *, name_error=None):
        self.open_file = open_file
        self.name_error = name_error
        self.calls = []

    def OpenConnection2(self, *args):  # noqa: N802 - COM method names
        self.calls.append("OpenConnection2")

    def BeginSession(self, company_file, mode):  # noqa: N802
        self.calls.append(("BeginSession", company_file))
        return "ticket"

    def GetCurrentCompanyFileName(self, ticket):  # noqa: N802
        self.calls.append("GetCurrentCompanyFileName")
        if self.name_error:
            raise self.name_error
        return self.open_file

    def ProcessRequest(self, ticket, qbxml):  # noqa: N802
        self.calls.append("ProcessRequest")
        return "<ok/>"

    def EndSession(self, ticket):  # noqa: N802
        self.calls.append("EndSession")

    def CloseConnection(self):  # noqa: N802
        self.calls.append("CloseConnection")


@pytest.fixture
def connect(monkeypatch):
    """Connect a QBConnection to a fake QuickBooks holding *open_file*."""
    pytest.importorskip("pythoncom")
    from qb_bridge.qb import connection as connection_mod

    def _connect(open_file, configured, **kwargs):
        rp = FakeRequestProcessor(open_file, **kwargs)
        monkeypatch.setattr(connection_mod.win32com.client, "Dispatch", lambda _name: rp)
        conn = connection_mod.QBConnection()
        return conn, rp, lambda: conn.connect(configured)

    return _connect


class TestSessionIsOnTheConfiguredFile:
    def test_the_configured_file_is_accepted(self, connect):
        conn, rp, go = connect(CONFIGURED.lower(), CONFIGURED)
        go()
        assert conn.session_open
        assert ("BeginSession", CONFIGURED) in rp.calls

    def test_another_company_is_refused_and_the_session_closed(self, connect):
        conn, rp, go = connect(OTHER, CONFIGURED)
        with pytest.raises(QBSessionError, match="Wrong company file") as err:
            go()
        assert "Some Other Company" in str(err.value) and "Acme Widgets" in str(err.value)
        assert not conn.session_open and not conn.connected
        assert rp.calls[-2:] == ["EndSession", "CloseConnection"]
        with pytest.raises(QBSessionError, match="No active QB session"):
            conn.process_request("<anything/>")
        assert "ProcessRequest" not in rp.calls

    def test_a_file_that_cannot_be_identified_is_refused(self, connect):
        import pythoncom

        conn, rp, go = connect(
            CONFIGURED, CONFIGURED, name_error=pythoncom.com_error(-1, "x", None, None)
        )
        with pytest.raises(QBSessionError, match="Could not confirm which company file"):
            go()
        assert not conn.session_open
        assert "EndSession" in rp.calls

    def test_the_refusal_is_not_something_to_retry(self, connect):
        from qb_bridge.qb.retry import classify

        _conn, _rp, go = connect(OTHER, CONFIGURED)
        with pytest.raises(QBSessionError) as err:
            go()
        assert classify(str(err.value)) is None

    def test_with_nothing_configured_the_open_file_is_used_and_named(self, connect, caplog):
        conn, _rp, go = connect(OTHER, "")
        with caplog.at_level("WARNING"):
            go()
        assert conn.session_open
        assert (
            "No company file is configured" in caplog.text and "Some Other Company" in caplog.text
        )
