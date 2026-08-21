# Web 演示页(demo-web)

对照拓扑图、点「下一步」逐步执行**真实**后端动作的演示页(共 14 步):组件注册 →
两侧获取 X.509-SVID → mTLS 互访 → hacker 攻击被拒 → 获取 JWT-SVID → 身份公网可验证(JWKS)
→ 零密钥云联邦(合法/冒充)→ 撤销注册后立即失效 → 恢复 → 标注未来可平滑演进到
CyberArk Idira 的组件。X.509-SVID 用于 mTLS 数据面,JWT-SVID 用于云联邦。

- 纯 HTTP、无外部依赖(仅 Python 标准库),默认监听 `0.0.0.0:8080`。
- 浏览器只发送「步骤 id」+ 语言,命令由后端从固定白名单(`server.py` 的 `STEPS`)执行。
- 每条命令的工作目录是仓库根目录,`docker compose` 会自动读取 `./.env`。
- 多语言:右上角可切换 **English / 简体中文**,默认 English(记忆在浏览器 localStorage)。
  界面文案、步骤标题/说明、以及第 7/12 步(`access-graph.sh` 走 `UILANG`)与第 13 步的
  说明性输出都会随语言切换;真实工具输出(docker、curl、SPIRE 等)保持原样。

## 运行(在 VM 的仓库根目录)

```bash
cd ~/identity
python3 demo-web/server.py            # 前台运行,Ctrl-C 停止
# 或后台:
nohup python3 demo-web/server.py > /tmp/demo-web.log 2>&1 &
```

自定义端口:`DEMO_PORT=9090 python3 demo-web/server.py`

然后浏览器打开 `http://<VM 公网 IP>:8080`。

## 安全

本页可执行真实命令(含 `spire-server entry delete`)。**务必**用 Azure NSG 将该端口
只对你的源地址开放,不要暴露到公网。演示结束后 `Ctrl-C` 或 `kill` 掉进程即可。

> 第 10 步会删除 `/agent` 注册条目、第 12 步会恢复。若演示中途退出,直接重跑第 12 步或
> `docker compose exec -T spire-server /opt/spire/scripts/register-entries.sh` 即可恢复。
> 第 14 步仅为架构说明(打印组件归属对照),不改动任何运行状态。
