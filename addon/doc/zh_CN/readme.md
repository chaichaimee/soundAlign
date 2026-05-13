<p align="center">
  <img src="https://www.nvaccess.org/files/nvda/documentation/userGuide/images/nvda.ico" alt="NVDA Logo" width="120">
</p>

<h1 align="center">SoundAlign</h1>

<p align="center">
  <strong>作者：</strong>Chai Chaimee<br>
  <strong>网址：</strong><a href="https://github.com/chaichaimee/soundAlign">https://github.com/chaichaimee/soundAlign</a>
</p>

<br>

## 概述
SoundAlign 是一款用于调整 NVDA 声音方向和表现方式的插件。它提供 **立体声声像、高级波形、多种淡入淡出算法，以及全局主音量控制**，可用于区分不同类型的提示音，并改善进度提示的听感和可辨识度。

如果你希望更容易区分错误提示、导航提示、插件提示音和进度提示，或者想单独调整这些声音的波形、频率范围和音量，这个插件提供了相应的设置选项。

<br>

## 快捷键
> **NVDA + Windows + S**：按一次，打开 SoundAlign 设置面板。  
> **NVDA + Windows + S**：连按两次，切换 SoundAlign 的开启/关闭状态（并播报当前状态）。

<br>

## 功能特性

### 声像定位
通过将声音定向到左、中、右声道，帮助区分不同类型的提示音，适用于：
* **错误提示**：通过声音位置快速分辨拼写错误或系统警告。
* **NVDA 音效**：在空间上区分标准屏幕阅读器声音，让反馈更清晰。
* **导航提示**：让边界提示音（如首行/末行）出现在恰当的位置。
* **插件提示音**：整理并区分来自你常用其他插件的提示音。
* **进度指示器**：通过可自定义的从左到右或从右到左声像移动，让进度变化更容易判断。

### 高级音频自定义
* **波形选择：** 可在 *正弦波、三角波、锯齿波、方波、原始提示音* 之间选择。
* **12 种专业淡入淡出算法：** *余弦、高斯、线性、指数、对数、S 曲线、正弦、四分之一正弦、半正弦、平方根、立方根、二次。*
* **主音量控制：** 为所有经 SoundAlign 处理的声音提供全局主音量滑块（0～100%）。
* **独立范围设置：** 可分别精细调整基础音量（0.1～1.0）与频率（110Hz～1760Hz）。
* **平滑声像移动：** 在快速声像变化时减少爆音。

### 进度播报
* **语音播报间隔**（1%、2%、5% 或 10%）。
* **提示音播报间隔**，用于补充反馈。
* **按时间播报**（每 N 秒一次）。
* **混合模式**，可同时使用语音和提示音反馈进度。

<br>

## 支持本项目
如果 SoundAlign 对你有帮助，欢迎支持项目持续开发。

<p align="center">
  <strong><a href="https://github.com/chaichaimee">通过 GitHub Sponsors 赞助</a></strong>
</p>

<p align="center">
  <sub>© 2026 Chai Chaimee · SoundAlign NVDA 插件 · 基于 GNU GPL v2+ 发布</sub>
</p>
