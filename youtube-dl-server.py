import sys
import subprocess
import os  # <<< 新增：用于读取文件系统

from starlette.status import HTTP_303_SEE_OTHER
from starlette.applications import Starlette
from starlette.config import Config
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route, Mount  # <<< 修改：引入 Mount 用于挂载静态目录
from starlette.templating import Jinja2Templates
from starlette.background import BackgroundTask
from starlette.staticfiles import StaticFiles # <<< 新增：用于提供文件下载服务

from yt_dlp import YoutubeDL, version

templates = Jinja2Templates(directory="templates")
config = Config(".env")

# 这里定义容器内的下载目录，和 Dockerfile/docker-compose 的挂载路径保持一致
DOWNLOAD_DIR = "/youtube-dl" 

app_defaults = {
    "YDL_FORMAT": config("YDL_FORMAT", cast=str, default="bestvideo+bestaudio/best"),
    "YDL_EXTRACT_AUDIO_FORMAT": config("YDL_EXTRACT_AUDIO_FORMAT", default=None),
    "YDL_EXTRACT_AUDIO_QUALITY": config(
        "YDL_EXTRACT_AUDIO_QUALITY", cast=str, default="192"
    ),
    "YDL_RECODE_VIDEO_FORMAT": config("YDL_RECODE_VIDEO_FORMAT", default=None),
    "YDL_OUTPUT_TEMPLATE": config(
        "YDL_OUTPUT_TEMPLATE",
        cast=str,
        default=f"{DOWNLOAD_DIR}/%(title).200s [%(id)s].%(ext)s",
    ),
    "YDL_ARCHIVE_FILE": config("YDL_ARCHIVE_FILE", default=None),
    "YDL_UPDATE_TIME": config("YDL_UPDATE_TIME", cast=bool, default=True),
}


async def dl_queue_list(request):
    # <<< 新增开始：获取文件列表逻辑
    files = []
    try:
        # 获取目录下所有文件
        with os.scandir(DOWNLOAD_DIR) as entries:
            # 过滤掉隐藏文件，按修改时间倒序排列（最新的在最上面）
            files = sorted(
                [entry.name for entry in entries if entry.is_file() and not entry.name.startswith('.')],
                key=lambda x: os.path.getmtime(os.path.join(DOWNLOAD_DIR, x)),
                reverse=True
            )
    except FileNotFoundError:
        files = []
    # <<< 新增结束

    return templates.TemplateResponse(
        "index.html", 
        {
            "request": request, 
            "ytdlp_version": version.__version__,
            "files": files  # <<< 修改：将文件列表传递给前端
        }
    )


async def redirect(request):
    return RedirectResponse(url="/youtube-dl")


async def q_put(request):
    form = await request.form()
    url = form.get("url").strip()
    ui = form.get("ui")
    options = {"format": form.get("format")}

    if not url:
        return JSONResponse(
            {"success": False, "error": "/q called without a 'url' in form data"}
        )

    task = BackgroundTask(download, url, options)

    print("Added url " + url + " to the download queue")

    if not ui:
        return JSONResponse(
            {"success": True, "url": url, "options": options}, background=task
        )
    return RedirectResponse(
        url="/youtube-dl?added=" + url, status_code=HTTP_303_SEE_OTHER, background=task
    )


async def update_route(scope, receive, send):
    task = BackgroundTask(update)

    return JSONResponse({"output": "Initiated package update"}, background=task)


def update():
    try:
        output = subprocess.check_output(
            [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"]
        )

        print(output.decode("utf-8"))
    except subprocess.CalledProcessError as e:
        print(e.output)


def get_ydl_options(request_options):
    request_vars = {
        "YDL_EXTRACT_AUDIO_FORMAT": None,
        "YDL_RECODE_VIDEO_FORMAT": None,
    }

    requested_format = request_options.get("format", "bestvideo")

    if requested_format in ["aac", "flac", "mp3", "m4a", "opus", "vorbis", "wav"]:
        request_vars["YDL_EXTRACT_AUDIO_FORMAT"] = requested_format
    elif requested_format == "bestaudio":
        request_vars["YDL_EXTRACT_AUDIO_FORMAT"] = "best"
    elif requested_format in ["mp4", "flv", "webm", "ogg", "mkv", "avi"]:
        request_vars["YDL_RECODE_VIDEO_FORMAT"] = requested_format

    ydl_vars = app_defaults | request_vars

    postprocessors = []

    if ydl_vars["YDL_EXTRACT_AUDIO_FORMAT"]:
        postprocessors.append(
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": ydl_vars["YDL_EXTRACT_AUDIO_FORMAT"],
                "preferredquality": ydl_vars["YDL_EXTRACT_AUDIO_QUALITY"],
            }
        )

    if ydl_vars["YDL_RECODE_VIDEO_FORMAT"]:
        postprocessors.append(
            {
                "key": "FFmpegVideoConvertor",
                "preferedformat": ydl_vars["YDL_RECODE_VIDEO_FORMAT"],
            }
        )

    return {
        "format": ydl_vars["YDL_FORMAT"],
        "postprocessors": postprocessors,
        "outtmpl": ydl_vars["YDL_OUTPUT_TEMPLATE"],
        "download_archive": ydl_vars["YDL_ARCHIVE_FILE"],
        "updatetime": ydl_vars["YDL_UPDATE_TIME"] == "True",
    }


def download(url, request_options):
    with YoutubeDL(get_ydl_options(request_options)) as ydl:
        ydl.download([url])


routes = [
    Route("/", endpoint=redirect),
    Route("/youtube-dl", endpoint=dl_queue_list),
    Route("/youtube-dl/q", endpoint=q_put, methods=["POST"]),
    Route("/youtube-dl/update", endpoint=update_route, methods=["PUT"]),
    # <<< 新增：挂载静态文件目录，这样你点击文件名就可以直接下载
    Mount("/downloads", app=StaticFiles(directory=DOWNLOAD_DIR), name="downloads"),
]

app = Starlette(debug=True, routes=routes)

print("Updating youtube-dl to the newest version")
update()