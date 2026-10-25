"""Public, credential-free errors for host stream workflows."""


class WorkflowError(ValueError):
    def __init__(self, code: str, message: str, *, status: int = 400):
        self.code = code
        self.status = status
        super().__init__(message)
