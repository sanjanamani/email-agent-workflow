"""
smtp_verify.py — SMTP mailbox verification without a paid API.

How it works:
  1. DNS MX lookup to find the mail server for the domain.
  2. Open a TCP connection on port 25 (SMTP).
  3. Issue EHLO → MAIL FROM → RCPT TO and read the response code.
  4. A 250 means the mailbox was accepted; 550/551/553 means it was rejected.

Caveats:
  - Gmail, Outlook, and Microsoft 365 use catch-all policies — they accept
    RCPT TO for any address to prevent harvesting. These come back as
    VerifyResult.CATCH_ALL after a catch-all probe.
  - Many ISPs and corporate networks block outbound port 25. In that case
    you'll get VerifyResult.UNKNOWN — fall back to Snov/Zerobounce.
  - Some servers do greylisting — they return 450 (temp fail) on first contact.
    We treat that as UNKNOWN rather than bouncing the address.

Typical use:
    from src.smtp_verify import verify_email, VerifyResult

    result = verify_email("jane.doe@clinic.com")
    if result == VerifyResult.VALID:
        # safe to send
    elif result == VerifyResult.INVALID:
        # hard bounce — skip this address
    else:
        # CATCH_ALL or UNKNOWN — use your own judgement
"""

import enum
import logging
import smtplib
import socket

import dns.resolver  # requires dnspython

logger = logging.getLogger("smtp_verify")

# Addresses we pretend to send FROM during the SMTP handshake
_PROBE_FROM = "verify@inara-ai-check.com"

# Random local parts used for catch-all detection
_CATCH_ALL_PROBE = "zzz_nonexistent_xyz_probe_abc"


class VerifyResult(enum.Enum):
    VALID = "valid"           # Server accepted RCPT TO — mailbox likely exists
    INVALID = "invalid"       # Server rejected RCPT TO (5xx) — hard bounce
    CATCH_ALL = "catch_all"   # Server accepts anything (Gmail, Outlook, etc.)
    UNKNOWN = "unknown"       # Port 25 blocked, DNS failure, timeout, etc.


def verify_email(email: str, timeout: int = 10) -> VerifyResult:
    """
    Verify a single email address via SMTP handshake.

    Args:
        email:   The address to check, e.g. "jane@clinic.com"
        timeout: TCP connection timeout in seconds (default 10)

    Returns:
        VerifyResult enum value
    """
    if not email or "@" not in email:
        return VerifyResult.INVALID

    local, domain = email.rsplit("@", 1)
    domain = domain.strip().lower()

    mx_host = _get_mx(domain)
    if not mx_host:
        logger.debug("smtp_verify: no MX record for %s", domain)
        return VerifyResult.UNKNOWN

    # First, probe a definitely-invalid address to detect catch-all servers
    catch_all_probe = f"{_CATCH_ALL_PROBE}@{domain}"
    probe_result = _smtp_rcpt(mx_host, catch_all_probe, timeout)

    if probe_result is True:
        # Server accepted a bogus address → catch-all
        logger.debug("smtp_verify: %s is a catch-all domain", domain)
        return VerifyResult.CATCH_ALL

    if probe_result is None:
        # Could not connect / port 25 blocked / timeout
        return VerifyResult.UNKNOWN

    # Now check the real address
    real_result = _smtp_rcpt(mx_host, email, timeout)

    if real_result is True:
        logger.debug("smtp_verify: %s accepted (valid)", email)
        return VerifyResult.VALID
    elif real_result is False:
        logger.debug("smtp_verify: %s rejected (invalid)", email)
        return VerifyResult.INVALID
    else:
        return VerifyResult.UNKNOWN


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_mx(domain: str) -> str:
    """Return the highest-priority MX hostname for domain, or empty string."""
    try:
        records = dns.resolver.resolve(domain, "MX", lifetime=5)
        # Lower preference value = higher priority
        best = sorted(records, key=lambda r: r.preference)[0]
        return str(best.exchange).rstrip(".")
    except Exception as exc:  # noqa: BLE001
        logger.debug("MX lookup failed for %s: %s", domain, exc)
        return ""


def _smtp_rcpt(mx_host: str, email: str, timeout: int) -> bool | None:
    """
    Open an SMTP connection to mx_host and issue RCPT TO for email.

    Returns:
        True   — server accepted (2xx)
        False  — server rejected (5xx)
        None   — connection failed, timeout, or temporary error (4xx)
    """
    try:
        with smtplib.SMTP(timeout=timeout) as smtp:
            smtp.connect(mx_host, 25)
            smtp.ehlo("inara-verify.local")
            smtp.mail(_PROBE_FROM)
            code, _ = smtp.rcpt(email)
            smtp.quit()

        if 200 <= code < 300:
            return True
        elif 500 <= code < 600:
            return False
        else:
            # 4xx = temporary failure (greylisting, rate limit, etc.)
            return None

    except smtplib.SMTPConnectError:
        logger.debug("smtp_verify: could not connect to %s:25", mx_host)
        return None
    except smtplib.SMTPServerDisconnected:
        logger.debug("smtp_verify: server %s disconnected unexpectedly", mx_host)
        return None
    except smtplib.SMTPException as exc:
        logger.debug("smtp_verify: SMTP error for %s: %s", email, exc)
        return None
    except (socket.timeout, OSError) as exc:
        logger.debug("smtp_verify: network error for %s: %s", mx_host, exc)
        return None
