# Docker Compose Deployment

这个项目推荐由 GitHub Actions 自动构建 Docker 镜像并推送到 GHCR。服务器只保留 `.env`、`docker-compose.yml` 和 Docker volume 里的运行数据；日常更新时执行 `docker compose pull && docker compose up -d`。

默认镜像：

```text
ghcr.io/liujunjie20240416/zhaojingying-cc:latest
```

## GitHub Actions 自动构建

仓库里的 `.github/workflows/docker-image.yml` 会在以下情况构建并推送镜像：

- push 到 `main`
- 手动点击 GitHub Actions 里的 `Run workflow`

它会推送两个 tag：

- `latest`：`main` 分支最新版本，服务器默认使用这个
- `sha-xxxxxxx`：按 commit SHA 生成的不可变版本，方便回滚

GHCR 使用仓库自带的 `GITHUB_TOKEN` 推送当前仓库关联的镜像，不需要额外配置 GitHub Secret。GitHub 官方文档说明，Actions workflow 可以用 `GITHUB_TOKEN` 发布关联到当前仓库的 package；GHCR 镜像可以用 `docker pull ghcr.io/NAMESPACE/IMAGE_NAME:tag` 拉取。

## 首次部署

```bash
git clone https://github.com/liujunjie20240416/zhaojingying-cc.git
cd zhaojingying-cc
cp .env.example .env
```

编辑服务器上的 `.env`，填入真实密钥。不要提交 `.env`。

如果 GHCR package 是 private，需要先在服务器登录一次：

```bash
docker login ghcr.io -u liujunjie20240416
```

密码填 GitHub Personal Access Token，至少需要 `read:packages` 权限。如果把 GHCR package 改成 public，则服务器可以不登录直接拉。

```bash
docker compose pull
docker compose up -d
```

应用默认监听 `8000` 端口：

- 站点/API: `http://服务器IP:8000`
- Django Admin: `http://服务器IP:8000/admin`

如果用宝塔反向代理，建议让域名代理到：

```text
http://127.0.0.1:8000
```

## 日常更新

本地修改后：

```bash
git add .
git commit -m "Update app"
git push origin main
```

服务器更新：

```bash
cd zhaojingying-cc
git pull
docker compose pull
docker compose up -d
```

`docker compose pull` 会从 GHCR 拉取 GitHub Actions 刚构建好的镜像；`docker compose up -d` 会用新镜像重启服务，并在容器启动时自动执行 Django migration。

如果服务器上的 `docker-compose.yml` 没变，日常甚至可以只执行：

```bash
docker compose pull
docker compose up -d
```

## 持久化数据

Compose 会创建三个 Docker volume：

| Volume | 容器路径 | 内容 |
| --- | --- | --- |
| `app_data` | `/app/data` | SQLite 数据库 |
| `app_media` | `/app/media` | 用户上传头像、背景图、语音文件 |
| `lancedb_storage` | `/app/ai/documents/lancedb_storage` | LanceDB 向量索引 |

这些数据不在 Git 里，也不会因为重新 build 镜像而丢失。

## 常用命令

```bash
# 查看日志
docker compose logs -f app

# 重启
docker compose restart app

# 进入容器
docker compose exec app sh

# 手动执行 migration
docker compose exec app python manage.py migrate
```

## 使用阿里云或腾讯云镜像仓库

GHCR 最简单。如果你要换成阿里云 ACR 或腾讯云 TCR，思路完全一样：

1. 在云厂商创建一个容器镜像仓库。
2. 在 GitHub 仓库 `Settings -> Secrets and variables -> Actions` 添加登录信息。
3. 把 workflow 里的 `registry`、`username`、`password` 和 `IMAGE_NAME` 换成云厂商地址。
4. 把 `docker-compose.yml` 里的 `image:` 换成同一个镜像地址。

阿里云 ACR 示例：

```yaml
env:
  IMAGE_NAME: registry.cn-hangzhou.aliyuncs.com/你的命名空间/zhaojingying-cc

- name: Log in to Aliyun ACR
  uses: docker/login-action@v3
  with:
    registry: registry.cn-hangzhou.aliyuncs.com
    username: ${{ secrets.ALIYUN_REGISTRY_USERNAME }}
    password: ${{ secrets.ALIYUN_REGISTRY_PASSWORD }}
```

腾讯云 TCR 个人版示例：

```yaml
env:
  IMAGE_NAME: ccr.ccs.tencentyun.com/你的命名空间/zhaojingying-cc

- name: Log in to Tencent Cloud TCR
  uses: docker/login-action@v3
  with:
    registry: ccr.ccs.tencentyun.com
    username: ${{ secrets.TENCENT_REGISTRY_USERNAME }}
    password: ${{ secrets.TENCENT_REGISTRY_PASSWORD }}
```

如果用腾讯云企业版 TCR，registry 通常是类似：

```text
你的实例名.tencentcloudcr.com
```

最终服务器命令仍然一样：

```bash
docker compose pull
docker compose up -d
```

## 从旧服务器文件迁移数据

如果服务器原来已有 `db.sqlite3`、`media/` 或 `ai/documents/lancedb_storage/`，先停止容器：

```bash
docker compose down
```

然后把旧数据复制进对应 volume。最简单的方式是临时启动一个 busybox 容器挂载 volume 后复制，或在宝塔文件管理里把旧数据放到项目目录，再执行一次性导入命令。

迁移前建议先备份原始文件。
