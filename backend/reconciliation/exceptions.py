"""Domain exceptions raised by services. The API maps each to an HTTP status (ARCHITECTURE §6.3)."""


class DomainError(Exception):
    status_code = 400

    def __init__(self, detail):
        super().__init__(detail)
        self.detail = detail


class InvalidFileError(DomainError):
    """The upload as a whole is unusable (missing column, bad encoding, no file). Nothing is saved."""

    status_code = 400


class OrganizationHeaderError(DomainError):
    status_code = 400


class NotFoundError(DomainError):
    status_code = 404


class ConflictError(DomainError):
    status_code = 409
