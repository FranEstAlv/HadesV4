from curl_cffi import requests as curl
from curl_cffi.const import CurlOpt


class ImpersonateSession(curl.Session):
    """Session de curl_cffi que aplica impersonate en cada request.

    curl_cffi tiene un bug/interacción con proxies IPRoyal: si se pasa
    ``impersonate`` al constructor de ``Session``, el túnel CONNECT al proxy
    falla con 504. La solución es pasar ``impersonate`` en cada llamada a
    ``request`` y dejar ``curl_options={CurlOpt.HTTPPROXYTUNNEL: 1}`` en la
    sesión.
    """

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
