#!/bin/bash

# --- 配置区域 ---
# 如果你的镜像名或容器名想改，可以在这里修改
IMAGE_NAME="my-ytdl-api"
CONTAINER_NAME="youtube-dl"
HOST_PORT=8080
DOWNLOAD_PATH="/home/core/youtube-dl" # 宿主机存储路径

echo "========== 1. 开始拉取 GitHub 最新代码 =========="
# 强制拉取并覆盖本地可能存在的微小改动（确保与仓库一致）
git fetch --all
git reset --hard origin/main

echo "========== 2. 停止并删除旧容器 (如果存在) =========="
docker stop $CONTAINER_NAME 2>/dev/null
docker rm $CONTAINER_NAME 2>/dev/null

echo "========== 3. 开始构建 Docker 镜像 (安装新依赖) =========="
# 使用 --no-cache 确保 requirements.txt 的修改被彻底执行
docker build --no-cache -t $IMAGE_NAME .

echo "========== 4. 启动新容器 =========="
# 确保宿主机下载目录存在
mkdir -p $DOWNLOAD_PATH

docker run -d \
  --name $CONTAINER_NAME \
  -p $HOST_PORT:8080 \
  -v $DOWNLOAD_PATH:/youtube-dl \
  --restart always \
  $IMAGE_NAME

echo "========== 5. 部署完成！当前状态： =========="
docker ps | grep $CONTAINER_NAME

echo "------------------------------------------------"
echo "提示: 正在为你展示 5 秒钟的实时日志以确认启动情况..."
echo "------------------------------------------------"
timeout 5s docker logs -f $CONTAINER_NAME