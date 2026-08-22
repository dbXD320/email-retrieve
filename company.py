"""Best-effort guess of the recipient's company. Step 5.

Rules, in priority order: signature block, then company mentions in the body,
then the email domain as a fallback.
"""


def guess(parsed: dict) -> tuple[str, str]:
    """Return (company, source). Both empty when nothing matches."""
    raise NotImplementedError("step 5")
