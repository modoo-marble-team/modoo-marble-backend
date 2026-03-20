from __future__ import annotations


class UserDomainError(Exception):
    status_code: int = 400

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class UserNotFoundError(UserDomainError):
    status_code = 404

    def __init__(self, detail: str = "User not found") -> None:
        super().__init__(detail)


class InvalidNicknameError(UserDomainError):
    status_code = 400

    def __init__(self, detail: str = "Invalid nickname") -> None:
        super().__init__(detail)


class NicknameAlreadyExistsError(UserDomainError):
    status_code = 409

    def __init__(self, detail: str = "Nickname already exists") -> None:
        super().__init__(detail)


class GuestNotAllowedError(UserDomainError):
    status_code = 403

    def __init__(self, detail: str = "Guest not allowed") -> None:
        super().__init__(detail)
