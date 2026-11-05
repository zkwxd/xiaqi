"""夏棋九路斗场 HTTP 服务。"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import ROOT, db_path
from .database import Database
from .rule_engine import RULES
from .service import GameError, GameService


class CreateGameBody(BaseModel):
    black_id: str
    white_id: str


class MoveBody(BaseModel):
    action: str
    x: int | None = None
    y: int | None = None
    vertex: str | None = None


class StepBody(BaseModel):
    think_ms: int = 2000


class AgentBody(BaseModel):
    name: str
    protocol: str
    command: str | None = None
    endpoint: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.service = GameService(Database(db_path()))
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="夏棋九路斗场", lifespan=lifespan)

    @app.exception_handler(GameError)
    async def _game_error(_request: Request, exc: GameError):
        return JSONResponse({"detail": exc.message}, status_code=exc.status)

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/rules")
    def rules():
        sample = f'"{sys.executable}" "{ROOT / "agents" / "stdio_random_agent.py"}"'
        return {**RULES, "sample_stdio_command": sample}

    @app.get("/api/agents")
    def agents(request: Request):
        return request.app.state.service.list_agents()

    @app.post("/api/agents")
    def add_agent(body: AgentBody, request: Request):
        return request.app.state.service.register_agent(
            body.name, body.protocol, body.command, body.endpoint
        )

    @app.delete("/api/agents/{agent_id}", status_code=204)
    def remove_agent(agent_id: str, request: Request):
        request.app.state.service.delete_agent(agent_id)
        return Response(status_code=204)

    @app.post("/api/games")
    def create_game(body: CreateGameBody, request: Request):
        return request.app.state.service.create_game(body.black_id, body.white_id)

    @app.get("/api/games")
    def list_games(request: Request):
        return request.app.state.service.list_games()

    @app.get("/api/games/{game_id}")
    def get_game(game_id: str, request: Request):
        return request.app.state.service.get_game(game_id)

    @app.post("/api/games/{game_id}/move")
    def play_move(game_id: str, body: MoveBody, request: Request):
        return request.app.state.service.play_move(game_id, body.action, body.x, body.y, body.vertex)

    @app.post("/api/games/{game_id}/step")
    def step(game_id: str, request: Request, body: StepBody | None = None):
        think = 2000 if body is None else body.think_ms
        return request.app.state.service.step(game_id, think)

    @app.post("/api/games/{game_id}/analyze")
    def analyze(game_id: str, request: Request):
        return request.app.state.service.analyze(game_id)

    @app.get("/api/games/{game_id}/sgf")
    def sgf(game_id: str, request: Request):
        text = request.app.state.service.sgf(game_id)
        filename = f"xiaqi-{game_id}.sgf"
        return Response(
            content=text,
            media_type="application/x-go-sgf; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    frontend = ROOT / "frontend"
    if frontend.exists():
        app.mount("/static", StaticFiles(directory=frontend), name="static")

    @app.get("/")
    def index():
        page = Path(frontend) / "index.html"
        if not page.exists():
            return JSONResponse({"detail": "界面文件还没有放好"}, status_code=404)
        return FileResponse(page)

    return app


app = create_app()
