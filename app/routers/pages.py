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


@router.get("/materials", response_class=HTMLResponse)
async def materials_page(request: Request):
    return templates.TemplateResponse("materials.html", {"request": request})


@router.get("/crews", response_class=HTMLResponse)
async def crews_page(request: Request):
    return templates.TemplateResponse("crews.html", {"request": request})


@router.get("/costs", response_class=HTMLResponse)
async def costs_page(request: Request):
    return templates.TemplateResponse("costs.html", {"request": request})


@router.get("/quotations", response_class=HTMLResponse)
async def quotations_page(request: Request):
    return templates.TemplateResponse("quotations.html", {"request": request})


@router.get("/inspections", response_class=HTMLResponse)
async def inspections_page(request: Request):
    return templates.TemplateResponse("inspections.html", {"request": request})
