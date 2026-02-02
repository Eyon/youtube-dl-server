import sys
import subprocess
import os
import uuid
import httpx
from starlette.status import HTTP_303_SEE_OTHER
from starlette.applications import Starlette
from starlette.config import Config
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route, Mount
from starlette.templating import Jinja2Templates
from starlette.background import BackgroundTask
from starlette.staticfiles import StaticFiles

from yt_dlp import YoutubeDL, version

# 配置
templates = Jinja2Templates(directory="templates")
config = Config(".env")
DOWNLOAD_DIR = "/youtube-dl"

# 用于存储任务状态的内存字典 (实际生产环境建议用 Redis)
# 格式: {"job_id": {"status": "pending/downloading/completed/failed", "filename": "xxx.mp4", "error": ""}}
jobs_status = {}

def download_worker(url, output_path, webhook_url, job_id):
    """后台下载任务核心逻辑"""
    jobs_status[job_id]["status"] = "downloading"
    
    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": output_path,
        "noplaylist": True,
        "merge_output_format": "mp4",
        "quiet": True
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        jobs_status[job_id]["status"] = "completed"
        print(f"Job {job_id} completed.")
    except Exception as e:
        jobs_status[job_id]["status"] = "failed"
        jobs_status[job_id]["error"] = str(e)
        print(f"Job {job_id} failed: {e}")

    # 保留 Webhook 功能 (可选)
    if webhook_url:
        try:
            with httpx.Client() as client:
                client.post(webhook_url, json={"job_id": job_id, "status": jobs_status[job_id]["status"]}, timeout=5.0)
        except: pass

# --- API 路由 ---

async def api_download(request):
    """请求下载"""
    try:
        data = await request.json()
    except:
        return JSONResponse({"success": False, "error": "Invalid JSON"}, status_code=400)

    url = data.get("url")
    if not url:
        return JSONResponse({"success": False, "error": "Missing url"}, status_code=400)

    job_id = str(uuid.uuid4())
    filename = f"{job_id}.mp4"
    output_path = os.path.join(DOWNLOAD_DIR, filename)

    # 初始化状态
    jobs_status[job_id] = {"status": "pending", "filename": filename, "error": None}

    task = BackgroundTask(download_worker, url, output_path, data.get("webhook"), job_id)
    return JSONResponse({"success": True, "job_id": job_id, "filename": filename}, background=task)

async def api_status(request):
    """查询下载状态 (iOS 轮询此接口)"""
    job_id = request.path_params.get("job_id")
    status_info = jobs_status.get(job_id)
    
    if not status_info:
        return JSONResponse({"success": False, "error": "Job not found"}, status_code=404)
    
    # 构造下载直链
    if status_info["status"] == "completed":
        status_info["download_url"] = f"/downloads/{status_info['filename']}"
    
    return JSONResponse(status_info)

# --- 原有 Web UI 路由 (略作修改以兼容) ---
async def dl_queue_list(request):
    files = []
    try:
        with os.scandir(DOWNLOAD_DIR) as entries:
            files = sorted([e.name for e in entries if e.is_file() and not e.name.startswith('.')],
                           key=lambda x: os.path.getmtime(os.path.join(DOWNLOAD_DIR, x)), reverse=True)
    except: pass
    return templates.TemplateResponse("index.html", {"request": request, "ytdlp_version": version.__version__, "files": files})

async def q_put(request):
    form = await request.form()
    url = form.get("url").strip()
    job_id = str(uuid.uuid4())
    jobs_status[job_id] = {"status": "pending", "filename": f"{job_id}.mp4"}
    task = BackgroundTask(download_worker, url, os.path.join(DOWNLOAD_DIR, f"{job_id}.mp4"), None, job_id)
    return RedirectResponse(url="/youtube-dl?added=" + url, status_code=HTTP_303_SEE_OTHER, background=task)

routes = [
    Route("/", endpoint=lambda r: RedirectResponse("/youtube-dl")),
    Route("/youtube-dl", endpoint=dl_queue_list),
    Route("/youtube-dl/q", endpoint=q_put, methods=["POST"]),
    Route("/api/download", endpoint=api_download, methods=["POST"]),
    Route("/api/status/{job_id}", endpoint=api_status, methods=["GET"]), # 新增：状态查询
    Mount("/downloads", app=StaticFiles(directory=DOWNLOAD_DIR), name="downloads"),
]

app = Starlette(debug=True, routes=routes)