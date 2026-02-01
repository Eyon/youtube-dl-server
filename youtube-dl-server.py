import sys
import subprocess
import os
import uuid
import httpx  # 用于发送 Webhook 通知
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

# 容器内固定的下载目录
DOWNLOAD_DIR = "/youtube-dl"

app_defaults = {
    "YDL_FORMAT": config("YDL_FORMAT", cast=str, default="bestvideo+bestaudio/best"),
    "YDL_EXTRACT_AUDIO_FORMAT": config("YDL_EXTRACT_AUDIO_FORMAT", default=None),
    "YDL_EXTRACT_AUDIO_QUALITY": config("YDL_EXTRACT_AUDIO_QUALITY", cast=str, default="192"),
    "YDL_RECODE_VIDEO_FORMAT": config("YDL_RECODE_VIDEO_FORMAT", default=None),
    "YDL_OUTPUT_TEMPLATE": config("YDL_OUTPUT_TEMPLATE", cast=str, default=f"{DOWNLOAD_DIR}/%(title).200s [%(id)s].%(ext)s"),
    "YDL_ARCHIVE_FILE": config("YDL_ARCHIVE_FILE", default=None),
    "YDL_UPDATE_TIME": config("YDL_UPDATE_TIME", cast=bool, default=True),
}

# --- 逻辑函数 ---

def get_files():
    """获取下载目录下的文件列表"""
    try:
        with os.scandir(DOWNLOAD_DIR) as entries:
            return sorted(
                [entry.name for entry in entries if entry.is_file() and not entry.name.startswith('.')],
                key=lambda x: os.path.getmtime(os.path.join(DOWNLOAD_DIR, x)),
                reverse=True
            )
    except Exception:
        return []

def download_worker(url, output_path, webhook_url, job_id):
    """后台下载任务核心逻辑"""
    # 强制指定格式和输出路径
    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": output_path,
        "noplaylist": True,
        "merge_output_format": "mp4"
    }

    status = "success"
    error_msg = None

    try:
        print(f"Starting download job {job_id} for URL: {url}")
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        print(f"Job {job_id} completed successfully.")
    except Exception as e:
        status = "failed"
        error_msg = str(e)
        print(f"Job {job_id} failed: {error_msg}")

    # 发送 Webhook 通知
    if webhook_url:
        payload = {
            "job_id": job_id,
            "url": url,
            "status": status,
            "filename": os.path.basename(output_path),
            "download_url": f"/downloads/{os.path.basename(output_path)}",
            "error": error_msg
        }
        try:
            with httpx.Client() as client:
                client.post(webhook_url, json=payload, timeout=10.0)
                print(f"Webhook sent to {webhook_url}")
        except Exception as we:
            print(f"Failed to send webhook for {job_id}: {we}")

# --- 路由处理函数 ---

async def dl_queue_list(request):
    """Web 页面展示"""
    added = request.query_params.get("added")
    return templates.TemplateResponse(
        "index.html", 
        {
            "request": request, 
            "ytdlp_version": version.__version__,
            "files": get_files(),
            "added": added
        }
    )

async def api_download(request):
    """新增的 JSON API 接口"""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "Invalid JSON body"}, status_code=400)

    url = data.get("url")
    webhook = data.get("webhook")
    
    if not url:
        return JSONResponse({"success": False, "error": "Missing 'url' parameter"}, status_code=400)

    # 生成 UUID 文件名
    job_id = str(uuid.uuid4())
    filename = f"{job_id}.mp4"
    output_path = os.path.join(DOWNLOAD_DIR, filename)

    # 启动后台任务
    task = BackgroundTask(download_worker, url, output_path, webhook, job_id)

    return JSONResponse({
        "success": True,
        "job_id": job_id,
        "filename": filename,
        "download_url": f"/downloads/{filename}"
    }, background=task)

async def q_put(request):
    """兼容原有的 Web UI 提交"""
    form = await request.form()
    url = form.get("url").strip()
    if not url:
        return JSONResponse({"success": False, "error": "No URL provided"})

    job_id = str(uuid.uuid4())
    output_path = os.path.join(DOWNLOAD_DIR, f"{job_id}.mp4")
    
    task = BackgroundTask(download_worker, url, output_path, None, job_id)
    return RedirectResponse(
        url="/youtube-dl?added=" + url, status_code=HTTP_303_SEE_OTHER, background=task
    )

async def redirect(request):
    return RedirectResponse(url="/youtube-dl")

def update():
    try:
        subprocess.check_output([sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"])
    except Exception as e:
        print(f"Update failed: {e}")

async def update_route(scope, receive, send):
    task = BackgroundTask(update)
    return JSONResponse({"output": "Initiated package update"}, background=task)

# --- 应用初始化 ---

routes = [
    Route("/", endpoint=redirect),
    Route("/youtube-dl", endpoint=dl_queue_list),
    Route("/youtube-dl/q", endpoint=q_put, methods=["POST"]),
    Route("/youtube-dl/update", endpoint=update_route, methods=["PUT"]),
    # API 接口
    Route("/api/download", endpoint=api_download, methods=["POST"]),
    # 静态文件访问接口 (下载最终文件)
    Mount("/downloads", app=StaticFiles(directory=DOWNLOAD_DIR), name="downloads"),
]

app = Starlette(debug=True, routes=routes)

print("Updating yt-dlp to the newest version...")
update()