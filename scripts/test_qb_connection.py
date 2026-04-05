"""Standalone QB SDK connection test.

Run on the machine hosting the QuickBooks company file to test
whether the qbXML SDK can connect.

Usage:
    uv run scripts/test_qb_connection.py

QuickBooks Desktop must be running with the company file open.
The first successful run will trigger a QB authorization dialog —
choose "Yes, always allow access".
"""
# /// script
# requires-python = ">=3.10"
# dependencies = ["pywin32"]
# ///

import struct
import sys

print(f"Python {sys.version}")
print(f"Architecture: {struct.calcsize('P') * 8}-bit")
print()

try:
    import win32com.client
except ImportError:
    print("ERROR: pywin32 not installed. Run: pip install pywin32")
    sys.exit(1)

# Step 1: Create COM object
print("[1] Creating QBXMLRP2.RequestProcessor COM object...")
try:
    rp = win32com.client.Dispatch("QBXMLRP2.RequestProcessor")
    print("    OK")
except Exception as e:
    print(f"    FAILED: {e}")
    print("\n    The QuickBooks SDK may not be installed.")
    sys.exit(1)

# Step 2: Open connection
print("[2] OpenConnection2...")
try:
    rp.OpenConnection2("QBBridge", "QuickBooks Bridge API", 1)  # localQBD
    print("    OK")
except Exception as e:
    print(f"    FAILED: {e}")
    sys.exit(1)

# Step 3: Begin session
print("[3] BeginSession (empty path = use whatever QB has open)...")
try:
    ticket = rp.BeginSession("", 2)  # qbFileOpenDoNotCare
    print(f"    OK! ticket={ticket}")
except Exception as e:
    desc = ""
    hresult = 0
    if hasattr(e, "args") and len(e.args) >= 3 and e.args[2]:
        desc = e.args[2][2]
        hresult = e.args[2][5] & 0xFFFFFFFF
    print(f"    FAILED: {desc or e}")
    print(f"    HRESULT: {hresult:#010x}")
    print()
    print("    Troubleshooting:")
    print("    - Is QuickBooks Desktop running with a company file open?")
    print("    - Edit > Preferences > Integrated Applications > Company Prefs")
    print("      Make sure 'Don't allow any applications' is UNCHECKED")
    print("    - Try logging into QB as Admin for the first authorization")
    rp.CloseConnection()
    sys.exit(1)

# Step 4: Test query — get company info
print("[4] Querying company info...")
request = """<?xml version="1.0" encoding="utf-8"?>
<?qbxml version="13.0"?>
<QBXML>
  <QBXMLMsgsRq onError="stopOnError">
    <CompanyQueryRq>
    </CompanyQueryRq>
  </QBXMLMsgsRq>
</QBXML>"""

try:
    response = rp.ProcessRequest(ticket, request)
    import xml.etree.ElementTree as ET

    root = ET.fromstring(response)
    company_name = root.findtext(".//CompanyName", "?")
    print(f"    Company: {company_name}")
except Exception as e:
    print(f"    Query failed: {e}")

# Step 5: Test account list
print("[5] Querying accounts (first 5)...")
request2 = """<?xml version="1.0" encoding="utf-8"?>
<?qbxml version="13.0"?>
<QBXML>
  <QBXMLMsgsRq onError="stopOnError">
    <AccountQueryRq>
      <MaxReturned>5</MaxReturned>
    </AccountQueryRq>
  </QBXMLMsgsRq>
</QBXML>"""

try:
    response2 = rp.ProcessRequest(ticket, request2)
    root2 = ET.fromstring(response2)
    accounts = root2.findall(".//AccountRet")
    print(f"    Got {len(accounts)} account(s):")
    for acct in accounts:
        name = acct.findtext("FullName", "?")
        atype = acct.findtext("AccountType", "?")
        print(f"      - {name} ({atype})")
except Exception as e:
    print(f"    Query failed: {e}")

# Cleanup
rp.EndSession(ticket)
rp.CloseConnection()

print()
print("=" * 50)
print("  ALL TESTS PASSED — SDK connection works!")
print("=" * 50)
