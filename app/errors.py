"""One error shape for every route: {"error": "<plain message>", "code": "<CODE>"}."""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

CODE_BY_STATUS = {400: "BAD_INPUT", 404: "NOT_FOUND", 413: "TOO_LARGE", 403: "KEYS_LOCAL_ONLY", 422: "BAD_INPUT"}

# HTTP status for each contract code
STATUS_BY_CODE = {
    "BAD_INPUT": 400, "TOO_LARGE": 413, "NOT_FOUND": 404, "KEYS_LOCAL_ONLY": 403,
    "AI_NOT_CONFIGURED": 503, "AI_KEY_INVALID": 502, "AI_BUSY": 503, "AI_FAILED": 502,
    "TICKET_REQUIRED": 401, "TICKET_INVALID": 401, "TICKET_EXPIRED": 401,
    "TICKET_USED_UP": 429, "DAILY_CAP_REACHED": 429, "RATE_LIMITED": 429,
}


class ApiError(Exception):
    def __init__(self, code: str, message: str, headers: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.headers = code, message, headers
        self.status = STATUS_BY_CODE.get(code, 500)


def install(app: FastAPI) -> None:
    def body(code: str, message: str) -> dict:
        return {"error": message, "code": code}

    @app.exception_handler(ApiError)
    async def _api(_: Request, exc: ApiError):
        return JSONResponse(body(exc.code, exc.message), status_code=exc.status, headers=exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = CODE_BY_STATUS.get(exc.status_code, "AI_FAILED" if exc.status_code >= 500 else "BAD_INPUT")
        msg = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return JSONResponse(body(code, msg), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _val(_: Request, exc: RequestValidationError):
        return JSONResponse(body("BAD_INPUT", "Some of the input is missing or invalid. Please check it and try again."),
                            status_code=400)
