"""Report engine — orchestrates report queries.

Currently a thin wrapper, but exists as the extension point for:
- Caching
- Multi-query composite reports
- Custom formatting rules
"""

from __future__ import annotations

from qb_bridge.qb import xml_builder, xml_parser
from qb_bridge.qb.session import QBSessionManager
from qb_bridge.qb.xml_parser import ReportData


async def run_report(
    session: QBSessionManager,
    *,
    request_type: str,
    body: dict,
) -> ReportData:
    """Execute a report query and return parsed data.

    Reports can run much longer than a CRUD call, so they use the session's
    larger ``report_timeout`` rather than the default request timeout.
    """
    request_xml = xml_builder.build_request(request_type, body)
    report_timeout = getattr(session, "report_timeout", None)
    response_xml = await session.execute(request_xml, timeout=report_timeout)
    return xml_parser.parse_report(response_xml)
