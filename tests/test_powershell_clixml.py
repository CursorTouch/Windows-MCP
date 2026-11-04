"""Errors from Windows PowerShell 5.1 reach the caller as text, not CLIXML.

Started with -EncodedCommand, powershell.exe serializes its error, warning,
verbose and debug streams to stderr as CLIXML whatever -OutputFormat says, so a
failing command used to return a "#< CLIXML <Objs ...>" document (often led by a
"Preparing modules for first use." progress record) in place of its message.
"""

import sys

import pytest

from windows_mcp.powershell.service import PowerShellExecutor
from windows_mcp.powershell.utils import decode_clixml

# Captured from powershell.exe 5.1 running Write-Warning, Write-Error and a
# native command that writes to stderr (its text precedes the document).
CAPTURED = (
    "#< CLIXML\r\n"
    "nat \r\n"
    '<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04">'
    '<Obj S="progress" RefId="0"><TN RefId="0">'
    "<T>System.Management.Automation.PSCustomObject</T><T>System.Object</T></TN><MS>"
    '<I64 N="SourceId">1</I64><PR N="Record"><AV>Preparing modules for first use.</AV>'
    "<AI>0</AI><Nil /><PI>-1</PI><PC>-1</PC><T>Completed</T><SR>-1</SR><SD> </SD></PR>"
    "</MS></Obj>"
    '<S S="warning">w1</S>'
    '<S S="Error">Write-Error : caf\u00e9 &lt;x&gt; &amp; "q" _x005F_x_x000D__x000A_</S>'
    '<S S="Error">    + FullyQualifiedErrorId : WriteErrorException_x000D__x000A_</S>'
    "</Objs>"
)


def test_decode_clixml_keeps_stream_text_and_drops_progress():
    assert decode_clixml(CAPTURED) == (
        "nat \r\n"
        "w1"
        'Write-Error : caf\u00e9 <x> & "q" _x\r\n'
        "    + FullyQualifiedErrorId : WriteErrorException\r\n"
    )


@pytest.mark.parametrize(
    "text",
    [
        "",
        "plain error text\r\n",
        "<Objs>not preceded by the marker</Objs>",
    ],
)
def test_decode_clixml_leaves_plain_text_alone(text):
    assert decode_clixml(text) == text


def test_decode_clixml_keeps_unparseable_document():
    text = '#< CLIXML\r\n<Objs Version="1.1.0.1"><S S="Error">a</Objs>'
    assert decode_clixml(text) == '<Objs Version="1.1.0.1"><S S="Error">a</Objs>'


@pytest.mark.skipif(sys.platform != "win32", reason="runs Windows PowerShell")
def test_failing_command_returns_readable_error():
    output, status = PowerShellExecutor.execute_command(
        "Get-Item -LiteralPath 'C:\\windows-mcp-no-such-item'", shell="powershell"
    )
    assert status != 0
    assert "CLIXML" not in output
    assert "<Objs" not in output
    assert "Cannot find path 'C:\\windows-mcp-no-such-item'" in output
