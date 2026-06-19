from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/ships", response_class=HTMLResponse)
async def ships_page(request: Request):
    return templates.TemplateResponse("ships.html", {"request": request})


@router.get("/docks", response_class=HTMLResponse)
async def docks_page(request: Request):
    return templates.TemplateResponse("docks.html", {"request": request})


@router.get("/tides", response_class=HTMLResponse)
async def tides_page(request: Request):
    return templates.TemplateResponse("tides.html", {"request": request})


@router.get("/tasks", response_class=HTMLResponse)
async def tasks_page(request: Request):
    return templates.TemplateResponse("tasks.html", {"request": request})


@router.get("/schedules", response_class=HTMLResponse)
async def schedules_page(request: Request):
    return templates.TemplateResponse("schedules.html", {"request": request})
