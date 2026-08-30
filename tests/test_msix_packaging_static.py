import struct
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "store" / "AppxManifest.template.xml"
WORKFLOW = ROOT / ".github" / "workflows" / "build-release.yml"
ASSETS = ROOT / "store" / "Assets"

NS = {
    "f": "http://schemas.microsoft.com/appx/manifest/foundation/windows10",
    "uap": "http://schemas.microsoft.com/appx/manifest/uap/windows10",
    "uap10": "http://schemas.microsoft.com/appx/manifest/uap/windows10/10",
    "desktop6": "http://schemas.microsoft.com/appx/manifest/desktop/windows10/6",
    "rescap": "http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities",
}


root = ET.parse(MANIFEST).getroot()
identity = root.find("f:Identity", NS)
assert identity is not None
assert identity.attrib == {
    "Name": "TullysAC6.AC6WinLossTracker",
    "Publisher": "CN=9F902EFC-0902-4564-8FE8-18CB1C21128F",
    "Version": "__MSIX_VERSION__",
    "ProcessorArchitecture": "x64",
}

application = root.find("f:Applications/f:Application", NS)
assert application is not None
assert application.attrib["Executable"] == "AC6-WinLoss-Tracker.exe"
assert application.attrib[f"{{{NS['uap10']}}}RuntimeBehavior"] == "packagedClassicApp"
assert application.attrib[f"{{{NS['uap10']}}}TrustLevel"] == "mediumIL"

family = root.find("f:Dependencies/f:TargetDeviceFamily", NS)
assert family is not None and family.attrib["Name"] == "Windows.Desktop"
capability = root.find("f:Capabilities/rescap:Capability", NS)
assert capability is not None and capability.attrib["Name"] == "runFullTrust"
capabilities = {item.attrib["Name"] for item in root.findall("f:Capabilities/rescap:Capability", NS)}
assert capabilities == {"runFullTrust", "unvirtualizedResources"}
virtualization = root.find("f:Properties/desktop6:FileSystemWriteVirtualization", NS)
assert virtualization is not None and virtualization.text == "disabled"

expected_assets = {
    "Square44x44Logo.png": (44, 44),
    "Square150x150Logo.png": (150, 150),
    "StoreLogo.png": (50, 50),
}
for name, dimensions in expected_assets.items():
    data = (ASSETS / name).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", name
    assert struct.unpack(">II", data[16:24]) == dimensions, name

workflow = WORKFLOW.read_text(encoding="utf-8")
for required in (
    "workflow_dispatch:",
    "tags:\n      - 'v*'",
    "actions/checkout@v4",
    "actions/setup-python@v5",
    'python -m PyInstaller --noconfirm --clean --onefile --noconsole',
    "AC6-WinLoss-Tracker-Windows.zip",
    "softprops/action-gh-release@v2",
    "MakeAppx.exe",
    "pack /d $staging",
    "unpack /p $env:MSIX_PATH",
    "steps.msix.outputs.path",
):
    assert required in workflow, required

print("MSIX packaging static checks passed")
