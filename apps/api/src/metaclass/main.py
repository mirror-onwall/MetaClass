from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from metaclass.core.application import build_services
from metaclass.core.config import settings
from metaclass.modules.classroom.api import create_router as create_classroom_router
from metaclass.modules.content.api import create_router as create_content_router
from metaclass.modules.materials.api import create_router as create_material_router
from metaclass.modules.presentation.api import create_router as create_presentation_router
from metaclass.modules.video.api import create_router as create_video_router


def create_app(data_dir: Path | None = None) -> FastAPI:
    services = build_services(
        data_dir or settings.data_dir,
        database_url=None if data_dir is not None else settings.resolved_database_url(),
        force_fake_llm=data_dir is not None,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            services.database.dispose()

    app = FastAPI(
        title="MetaClass API",
        version="0.1.0",
        description="可运行的 Read → Plan → Run 极简教学闭环",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.services = services

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(create_material_router(services.materials))
    app.include_router(create_content_router(services.contents))
    app.include_router(create_presentation_router(services.presentations))
    app.include_router(create_classroom_router(services.classrooms))
    app.include_router(create_video_router(services.videos))

    web_dist = Path(__file__).resolve().parents[3] / "web" / "dist"
    if web_dist.is_dir():
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")

    return app


app = create_app()
