# 夏棋 · 九路斗场

9×9 围棋对局。可以自己下，可以接自己的 AI，也可以让两个 AI 斗一场。终局按中国数子法结算，并给出缺点和棋谱。

## 规则

- 棋盘 9×9，黑先。
- 中国数子法。面积只算盘上的子和围住的空，提子不重复加入面积。
- 黑贴 3¾ 子，也就是 7½ 目。
- 胜子数 = 贴后面积差 ÷ 2。贴后面积差 = 黑方面积 − 白方面积 − 7.5。
- 落子、提子、禁自杀、全局同形都由 `RuleEngine` 判定。
- 双方连续两次 PASS 终局。死子必须实际提掉，终局不自动删子。
- 正式 AI 协议只有 `PLACE`、`PASS`、`RESIGN`。`END_GAME` 会判负。
- 每步硬超时 10 秒。超时、非法落子、协议错误由竞赛外层判负。人类点到禁着只会退回，不判负。

## 运行

```powershell
.\run.ps1
```

浏览器打开 http://127.0.0.1:8000 。

也可以手动：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

测试：

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
```

## 接入 AI

内置三个：随机、启发式、蒙特卡洛。蒙特卡洛的每步模拟都调用原来的规则引擎。

自己的程序用本地命令或 HTTP。请求体是一行 JSON，`allowed_actions` 里没有 `END_GAME`。在 `time_limit_ms`（10000）内回复一行：

```json
{"action": "PLACE", "vertex": "E5"}
```

`vertex` 用 GTP 坐标，列名跳过 I，行号从下往上。也可以写 `{"action":"PLACE","x":4,"y":4}`。虚手是 `{"action":"PASS"}`，认输是 `{"action":"RESIGN"}`。

示例命令：

```text
python agents/stdio_random_agent.py
```

界面里的「填入示例」会带上当前解释器的绝对路径。命令在本机执行。

对局结束后可以导出 SGF。文件里写有贴目、胜子数和手数评注。

## 目录

- `backend/app/rule_engine.py`：规则与数子
- `backend/app/protocol.py`：竞赛外层
- `backend/app/agents.py`：内置 AI 和外部接入
- `backend/app/analysis.py`：缺点分析
- `backend/app/sgf_export.py`：棋谱
- `frontend/`：对局界面
- `agents/stdio_random_agent.py`：外部 AI 示例
