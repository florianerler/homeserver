"""Kurze, geheimnisfreie Fehlergruende fuer die Anzeige."""
import errno
import socket
import ssl


def classify_os_error(exc):
    """Netzwerkfehler (urllib/socket/ssl) -> Grund-Code.

    Bewusst ueber Ausnahmetyp und errno statt ueber Fehlertexte: die sind je nach
    Sprache und Python-Version verschieden ('no route to host' wurde frueher als
    'refused' eingeordnet).
    """
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(exc, socket.gaierror):
        return "dns"
    if isinstance(exc, ssl.SSLError):
        return "tls"
    if isinstance(exc, ConnectionRefusedError):
        return "refused"
    code = getattr(exc, "errno", None)
    if code in (errno.EHOSTUNREACH, errno.ENETUNREACH, errno.EHOSTDOWN, errno.ENETDOWN):
        return "unreachable"
    if code == errno.ECONNREFUSED:
        return "refused"
    if code == errno.ETIMEDOUT:
        return "timeout"
    return "error"


def classify_ssh_error(stderr_text):
    """Text von ssh (stderr) -> Grund-Code."""
    s = stderr_text.lower()
    if "host key verification failed" in s or "remote host identification" in s:
        return "hostkey"
    if "permission denied" in s:
        return "auth"
    if "no route to host" in s or "network is unreachable" in s or "host is down" in s:
        return "unreachable"
    if "timed out" in s or "timeout" in s:
        return "timeout"
    if "refused" in s:
        return "refused"
    if "could not resolve" in s:
        return "dns"
    return "ssh"
