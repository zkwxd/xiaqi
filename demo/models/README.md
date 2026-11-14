# 冻结模型来源

本目录随 Demo 固定发布，与原项目中后续修改的提交文件分开。

- 冻结实验：`upgrade-20261109`，发布于 2026 年 11 月 9 日。
- `bot.exe`：该实验发布的 Windows 原生程序，两份权重共用此程序。
- `trained/weights.txt`：最终候选 `candidate-003` 导出的强化权重。
- `baseline/weights.txt`：同一实验保存的强化前权重。
- `main.cpp`：从冻结实验的提交 ZIP 中原样恢复的 C++ 源码。

原提交 ZIP 的 SHA256：`8fbeac88ad2bb399ecea14bb6073980a31d58b289b4f60da9f2f442df85ed518`。

C++ 源码的 SHA256：`dc40b2e5b2d4ec6b3780b41d9f850dd72f90e95225f89f39f737fa085a6ac7ce`。

程序及两份权重的校验和记录在 `manifest.json`，运行前会核对。Git 对权重文件和冻结源码禁用换行转换，以保留原始字节。

如需自行编译，可在安装了 MinGW g++ 的 Windows 上执行：

```powershell
g++ -std=c++17 -O2 -static demo/models/main.cpp -o demo/models/bot.exe
```

编译器和选项可能改变程序二进制校验和；重新编译后需重新核对并更新 `manifest.json`，不能把新程序视为原冻结程序。

实时演示明确传入连续两次 PASS 终局、每手 32 次搜索和 2.5 秒搜索预算。独立评测的模型权重相同，双方每手搜索 64 次。
