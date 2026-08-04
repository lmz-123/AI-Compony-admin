# AI-Compony-admin

AI Company 的独立后台管理台。它不运行在 AI Company team 容器内部，而是作为单独服务部署，避免可写控制面和 agent 执行面耦合。

默认端口：

```text
8766
```

默认读取：

```text
/root/AI--compony/team-data/claudeteam.toml
/root/AI--compony/team-data/state
```

支持：

- 查看 agent 状态、心跳、任务、inbox、radio、日志
- 实时查看每个 agent 的 tmux pane 输出
- 新增 agent
- 编辑 agent 配置
- 删除 agent
- hire / fire / restart

## Docker 部署

在服务器执行：

```bash
cd /root
git clone git@github.com:lmz-123/AI-Compony-admin.git
cd /root/AI-Compony-admin

docker compose up -d --build
```

检查：

```bash
curl -fsS http://127.0.0.1:8766/api/admin/state | head -c 1000
```

外网访问需要放行端口：

```bash
ufw allow 8766/tcp
ufw status
```

然后访问：

```text
http://你的服务器公网IP:8766/
```

如果公网开放，建议放在 Nginx 后面并加 Basic Auth / IP 白名单 / HTTPS。

## systemd 部署

如果不想用 Docker，也可以直接跑在宿主机：

```bash
cd /root
git clone git@github.com:lmz-123/AI-Compony-admin.git
cd /root/AI-Compony-admin
bash scripts/install_systemd.sh /root/AI-Compony-admin
```

检查：

```bash
systemctl status ai-company-admin --no-pager
curl -fsS http://127.0.0.1:8766/api/admin/state | head -c 1000
```

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `AI_COMPANY_ROOT` | `/root/AI--compony` | AI Company 主项目目录 |
| `AI_COMPANY_STATE_DIR` | `$AI_COMPANY_ROOT/team-data/state` | 运行状态目录 |
| `AI_COMPANY_CONFIG` | `$AI_COMPANY_ROOT/team-data/claudeteam.toml` | team 配置文件 |
| `AI_COMPANY_ADMIN_HOST` | `127.0.0.1` | 监听地址 |
| `AI_COMPANY_ADMIN_PORT` | `8766` | 监听端口 |
| `AI_COMPANY_CONTAINER` | 空 | claudeteam 容器名；为空时自动探测 |

## 和主项目的关系

主项目 `lmz-123/AI--compony` 只保留 agent runtime、Feishu、monitor、doctor、radio、learn 等能力。

本项目是独立可写控制面，通过以下方式工作：

- 读取主项目 state/config 文件
- 修改 `claudeteam.toml` 中的 agent 配置
- 通过宿主机挂载进容器的 `/usr/bin/docker` 和 Docker socket，进入运行中的 `claudeteam` 容器执行 `claudeteam hire/fire/restart`

因此二者需要分别更新、分别部署。
