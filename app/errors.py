class MercosError(Exception):
    def __init__(self, message: str, *, status_code: int = 502, details=None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details


class MercosConfigurationError(MercosError):
    def __init__(self):
        super().__init__("Credenciais Mercos não configuradas", status_code=503)


class MercosRateLimitError(MercosError):
    def __init__(self):
        super().__init__("Limite de requisições da Mercos excedido", status_code=503)

