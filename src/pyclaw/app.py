from __future__ import annotations


def main() -> None:
    import uvicorn

    uvicorn.run("pyclaw.app:create_app", factory=True, host="0.0.0.0", port=8000, reload=True)


def create_app():
    from fastapi import FastAPI

    app = FastAPI(title="PyClaw", version="0.1.0")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


if __name__ == "__main__":
    main()
