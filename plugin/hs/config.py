"""Konfiguration aus ~/.config/homeserver/config.ini (chmod 600)."""
import configparser
import os
import re
import ssl
from urllib.parse import urlsplit

DEFAULT_CONFIG = "~/.config/homeserver/config.ini"
IFACE_RE = re.compile(r"^[A-Za-z0-9._-]{1,15}$")


class ConfigError(Exception):
    """Der Text ist ein Grund-Code (wird so im Popup uebersetzt angezeigt)."""


def load_config(path):
    path = os.path.expanduser(path)
    cp = configparser.ConfigParser(interpolation=None)  # '%' im Passwort erlaubt
    if not cp.read(path):
        raise ConfigError("config-missing")
    try:
        ssh, qb = cp["ssh"], cp["qbittorrent"]
        cfg = {
            "ssh_host": ssh["host"].strip(),
            "ssh_user": ssh["user"].strip(),
            "ssh_key": os.path.expanduser(ssh["key"].strip()),
            "ssh_known_hosts": os.path.expanduser(ssh["known_hosts"].strip()),
            "main_iface": ssh.get("main_iface", "nic0").strip(),
            "qb_url": qb["url"].strip().rstrip("/"),
            "qb_user": qb["username"].strip(),
            "qb_pass": qb["password"].strip(),
            "qb_ca": os.path.expanduser(qb.get("ca_file", "").strip()),
        }
    except KeyError:
        raise ConfigError("config-incomplete")
    if not IFACE_RE.match(cfg["main_iface"]):
        raise ConfigError("config-iface")
    if urlsplit(cfg["qb_url"]).scheme not in ("http", "https"):
        raise ConfigError("config-url")
    if cfg["qb_ca"] and not os.path.isfile(cfg["qb_ca"]):
        raise ConfigError("config-ca")
    if cfg["qb_url"].startswith("https://"):
        try:
            tls_context(cfg["qb_ca"])           # kaputte CA-Datei schon beim Start melden
        except (ssl.SSLError, OSError):
            raise ConfigError("config-ca")
    return cfg


def tls_context(ca_file):
    """TLS-Kontext mit voller Pruefung (Zertifikat und Hostname).

    Mit ca_file wird gezielt dieser CA vertraut (z. B. eine eigene CA), sonst dem
    System-Store. Die Pruefung wird nie abgeschaltet.
    """
    ctx = ssl.create_default_context(cafile=ca_file or None)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx
