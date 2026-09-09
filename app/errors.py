class MercosError(Exception):
    def __init__(self, message: str, *, status_code: int = 502, details=None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details


class MercosConfigurationError(MercosError):
    def __init__(self):
        super().__init__("Credenciais Mercos não configuradas", status_code=503)


class MercosRateLimitError(MercosError):
    def __init__(self, retry_after: float = 30):
        wait = max(float(retry_after), 1.0)
        super().__init__(
            "Too Many Requests",
            status_code=429,
            details={"tempo_ate_permitir_novamente": wait},
        )
        self.retry_after = wait

