# 在线文档服务

这是一个轻量级 Markdown 在线文档服务。启动后会自动扫描当前目录及子目录中的 Markdown 文档，并在浏览器中渲染文档、目录树、正文大纲、搜索结果和 Mermaid 图表。

## 功能

- 自动发现当前目录及子目录下的 `.md`、`.markdown` 文件。
- 在线渲染 Markdown 内容。
- 支持 Mermaid 代码块渲染。
- 支持文档树浏览、全文搜索、正文大纲和相对图片路径。
- 后台定时执行 `git pull --ff-only`，保持 Git 仓库文件最新。
- 支持 Linux、Windows 和 Docker Compose 启动。

## 本地启动

首次运行可先创建虚拟环境并安装依赖。当前服务端只使用 Python 标准库，`requirements.txt` 保留为统一依赖入口，后续新增依赖时直接维护该文件。

Linux 或 macOS:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Linux 或 macOS:

```bash
./start-docs.sh
```

Windows PowerShell:

```powershell
.\start-docs.ps1
```

Windows CMD:

```cmd
start-docs.cmd
```

默认访问地址:

```text
http://127.0.0.1:8090/
```

常用参数:

```bash
./start-docs.sh --port 8091 --no-open
./start-docs.sh --git-pull-interval 600
./start-docs.sh --no-git-pull
```

## Docker Compose 启动

Docker 镜像会在构建时安装 `requirements.txt` 中的 Python 依赖。

```bash
WEB_DOCS_UID=$(id -u) WEB_DOCS_GID=$(id -g) docker compose up --build
```

Compose 会把 `WEB_DOCS_UID` 和 `WEB_DOCS_GID` 传给容器入口脚本。入口脚本会在容器内创建匹配的用户和用户组记录，然后用该 UID/GID 运行文档服务，避免 Git SSH 在数字 UID 没有系统用户记录时失败。

默认访问地址:

```text
http://127.0.0.1:8090/
```

如需修改宿主机端口:

```bash
WEB_DOCS_PUBLISHED_PORT=8091 WEB_DOCS_UID=$(id -u) WEB_DOCS_GID=$(id -g) docker compose up --build
```

如果 Git remote 使用 SSH 地址，例如 `git@github.com:owner/repo.git`，容器内需要能读取 SSH key 和 `known_hosts`。Linux/macOS 可挂载宿主机 SSH 配置:

```yaml
services:
  web-docs:
    volumes:
      - ./:/docs
      - ~/.ssh:/tmp/.ssh:ro
```

如果不挂载 SSH 配置，容器内即使已安装 `ssh`，`git pull` 也可能因为缺少私钥、缺少 `known_hosts` 或仓库访问权限而失败。此时日志通常会显示 `Permission denied (publickey)`、`Host key verification failed` 或 `Could not read from remote repository`。

更新 Dockerfile 后需要重新构建镜像:

```bash
WEB_DOCS_UID=$(id -u) WEB_DOCS_GID=$(id -g) docker compose up --build
```

## Git 自动更新

服务默认启用后台 Git 自动拉取，每 300 秒执行一次:

```bash
git pull --ff-only
```

这只允许快进更新，不会自动 merge，也不会覆盖本地冲突变更。若工作区存在未提交修改、分支无法快进、认证失败或网络不可用，服务会记录错误并在下一轮继续重试。

可用环境变量:

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `WEB_DOCS_GIT_PULL` | `1` | 设置为 `0`、`false`、`no` 或 `off` 可关闭自动拉取 |
| `WEB_DOCS_GIT_PULL_INTERVAL` | `300` | 自动拉取间隔，单位秒 |
| `WEB_DOCS_GIT_PULL_TIMEOUT` | `120` | 单次拉取超时时间，单位秒 |
| `WEB_DOCS_GIT_PULL_REMOTE` | 空 | 可选 Git remote，不设置则使用当前分支 upstream |
| `WEB_DOCS_GIT_PULL_BRANCH` | 空 | 可选 Git branch，设置 branch 但不设置 remote 时默认使用 `origin` |

示例:

```bash
WEB_DOCS_GIT_PULL_INTERVAL=60 ./start-docs.sh --no-open
```

指定远端和分支:

```bash
./start-docs.sh --git-pull-remote origin --git-pull-branch main
```

## 文档规则

- 文档根目录是启动脚本所在目录，Docker 中是 `/docs`。
- `.git`、`.gradle`、`.idea`、`.pytest_cache`、`build`、`.venv`、`node_modules` 会被排除。
- 相对 Markdown 链接会被重写为在线文档链接。
- 相对图片路径会通过服务端 `/file/` 路径加载。

## Mermaid 示例

```mermaid
flowchart LR
  Start[启动服务] --> Scan[扫描文档]
  Scan --> Render[渲染 Markdown]
  Render --> Pull[后台定时 git pull]
  Pull --> Scan
```

## 常用命令

检查脚本帮助:

```bash
./start-docs.sh --help
```

检查 Python 语法:

```bash
python3 -m py_compile serve-docs.py
```

验证 Docker Compose 配置:

```bash
docker compose config
```

停止 Docker Compose 服务:

```bash
docker compose down
```
