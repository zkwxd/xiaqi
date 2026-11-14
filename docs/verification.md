# Demo 发布验证 · 2026-11-14

本分支使用独立工作目录验证，不依赖原训练目录或当前修改中的提交程序。

| 检查 | 结果 |
| --- | --- |
| 后端测试 | 29 项通过 |
| 冻结文件 | C++ 源码、Windows 程序及两份权重校验通过 |
| 历史评测棋谱 | 全部 200 局逐手重放，终局与胜负一致 |
| 浏览器完整流程 | 通过，下列九组操作均正常 |
| 独立 ZIP | 校验、解压、页面、模型、棋谱和真实 AI 合法落子均通过 |

浏览器流程覆盖：

1. 加载真实模型状态和冻结评测数据。
2. 人类在 A1 落子，强化模型应答，坐标方向正确。
3. 人类执白时，AI 自动执黑开局。
4. 强化版对原版自动对战、暂停及单步。
5. 全部 200 局回放、139 胜 / 61 负筛选、播放、拖动、数子与 SGF 导出。
6. 成果图表和从任意评测对局直接进入回放。
7. 刷新后恢复本地保存的真实对局。
8. 390 像素手机布局，触摸切换和回放，无横向溢出。
9. 浏览器无 JavaScript 或控制台错误。

可重复执行：

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests -q
.\.venv\Scripts\python.exe demo\build_demo.py --package
.\.venv\Scripts\python.exe demo\verify_package.py
node demo/check-demo.cjs
```

浏览器检查需要安装 Playwright 和对应 Chromium，并先启动 Demo 服务。`XIAQI_DEMO_URL` 可指定服务地址。

压缩包 SHA256 见根目录 `xiaqi-demo-windows.sha256`；模型校验值见 `demo/models/manifest.json`。实时对弈每手 32 次搜索，历史主评测每手 64 次搜索，结果不作为线上积分。
