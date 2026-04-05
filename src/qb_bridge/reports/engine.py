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
    """Execute a report query and return parsed data."""
    request_xml = xml_builder.build_request(request_type, body)
    response_xml = await session.execute(request_xml)
    return xml_parser.parse_report(response_xml)
