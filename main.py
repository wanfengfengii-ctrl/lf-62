from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from app.database import engine, Base
from app.routers import ships, docks, tides, tasks, schedules, pages, materials, crews


Base.metadata.create_all(bind=engine)

app = FastAPI(title="潮汐船坞修船排程系统")

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

app.include_router(ships.router, prefix="/api/ships", tags=["ships"])
app.include_router(docks.router, prefix="/api/docks", tags=["docks"])
app.include_router(tides.router, prefix="/api/tides", tags=["tides"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])
app.include_router(schedules.router, prefix="/api/schedules", tags=["schedules"])
app.include_router(materials.router, prefix="/api/materials", tags=["materials"])
app.include_router(crews.router, prefix="/api/crews", tags=["crews"])
app.include_router(pages.router, tags=["pages"])


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})
