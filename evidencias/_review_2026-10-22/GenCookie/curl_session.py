"""Session de curl_cffi compatible con proxies http/https con túnel CONNECT.

Problema real: si se pasa ``impersonate`` al constructor de ``Session`` y
además se configura un proxy, el túnel HTTPS (CONNECT) puede fallar o ser
ignorado por curl. El síntoma es que la petición sale directa o recibe un
504 del proxy.

Solución: dejar la sesión con ``CurlOpt.HTTPPROXYTUNNEL`` activo y aplicar
``impersonate`` en cada request, no en el constructor.
"""
from curl_cffi import requests as curl
from curl_cffi.const import CurlOpt


class ImpersonateSession(curl.Session):
    """Session de curl_cffi que aplica impersonate en cada request."""

    def __init__(self, impersonate=None, **kwargs):
        if "curl_options" not in kwargs:
            kwargs["curl_options"] = {}
        kwargs["curl_options"][CurlOpt.HTTPPROXYTUNNEL] = 1
        super().__init__(**kwargs)
        self._impersonate = impersonate

    def request(self, method, url, **kwargs):
        if self._impersonate and "impersonate" not in kwargs:
            kwargs["impersonate"] = self._impersonate
        return super().request(method, url, **kwargs)
