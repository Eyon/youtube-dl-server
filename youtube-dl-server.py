import sys
import subprocess
import os
import uuid
import httpx
from starlette.status import HTTP_401_UNAUTHORIZED
from starlette.applications import Starlette
from starlette.config import Config
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, Mount
from starlette.background import BackgroundTask
from starlette.staticfiles import StaticFiles

from yt_dlp import YoutubeDL, version

# 配置
config = Config(".env")
DOWNLOAD_DIR = "/youtube-dl"

# --- 权限配置 ---
# 默认 Token：xt_8f2d9e1a5b6c4d7e3a2f1b9c8d7e6a5b
API_TOKEN = config("API_TOKEN", cast=str, default="xt_8f2d9e1a5b6c4d7e3a2f1b9c8d7e6a5b")

def check_auth(request):
    """校验 API Token"""
    auth_header = request.headers.get("Authorization")
    if not auth_header or auth_header != f"Bearer {API_TOKEN}":
        return False
    return True

# 任务状态存储
jobs_status = {}

def download_worker(url, output_path, webhook_url, job_id):
    """后台下载任务"""
    jobs_status[job_id]["status"] = "downloading"
    
    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": output_path,
        "noplaylist": True,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        jobs_status[job_id]["status"] = "completed"
        print(f"[SUCCESS] Job {job_id} done.")
    except Exception as e:
        jobs_status[job_id]["status"] = "failed"
        jobs_status[job_id]["error"] = str(e)
        print(f"[ERROR] Job {job_id} failed: {e}")

    # Webhook 通知
    if webhook_url:
        try:
            with httpx.Client() as client:
                client.post(webhook_url, json={
                    "job_id": job_id, 
                    "status": jobs_status[job_id]["status"],
                    "filename": jobs_status[job_id]["filename"],
                    "download_url": f"/downloads/{jobs_status[job_id]['filename']}" if jobs_status[job_id]["status"] == "completed" else None
                }, timeout=5.0)
        except:
            pass

# --- API 接口 ---

async def api_download(request):
    """触发下载 API"""
    if not check_auth(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        data = await request.json()
    except:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    url = data.get("url")
    if not url:
        return JSONResponse({"error": "Missing url"}, status_code=400)

    job_id = str(uuid.uuid4())
    filename = f"{job_id}.mp4"
    output_path = os.path.join(DOWNLOAD_DIR, filename)

    # 初始化任务状态
    jobs_status[job_id] = {
        "status": "pending",
        "filename": filename,
        "error": None
    }

    task = BackgroundTask(download_worker, url, output_path, data.get("webhook"), job_id)
    return JSONResponse({
        "success": True, 
        "job_id": job_id,
        "filename": filename
    }, background=task)

async def api_status(request):
    """查询状态 API"""
    if not check_auth(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    job_id = request.path_params.get("job_id")
    status_info = jobs_status.get(job_id)
    
    if not status_info:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    
    # 构建完整响应
    response_data = {
        "job_id": job_id,
        "status": status_info["status"],
        "filename": status_info["filename"],
        "error": status_info["error"]
    }
    
    if status_info["status"] == "completed":
        response_data["download_url"] = f"/downloads/{status_info['filename']}"
    
    return JSONResponse(response_data)

async def home(request):
    """根路径：仅作为运行状态展示，不提供 UI"""
    return JSONResponse({
        "service": "youtube-dl-api",
        "status": "running",
        "version": version.__version__
    })

# --- 路由配置 ---

routes = [
    Route("/", endpoint=home),
    Route("/api/download", endpoint=api_download, methods=["POST"]),
    Route("/api/status/{job_id}", endpoint=api_status, methods=["GET"]),
    # 静态文件下载（这个不需要 Token，因为 UUID 已经是天然的屏障）
    Mount("/downloads", app=StaticFiles(directory=DOWNLOAD_DIR), name="downloads"),
]

app = Starlette(debug=False, routes=routes)

# 启动自动更新 yt-dlp
def update():
    try:
        subprocess.check_output([sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"])
        print("yt-dlp updated.")
    except:
        pass

update()